"""Reglas compartidas por los portales publicos de eComFax y Recordia."""

import hashlib
import hmac
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.api import APIValidationError
from app.models import Brand, HelpCenter, PublicTicketAttempt
from app.services.attachments import AttachmentService
from app.services.email_ingestion import EmailIngestionService
from app.services.tickets import TicketService


LOCALE_ALIASES = {
    "es": "es", "es-es": "es",
    "en": "en", "en-gb": "en", "en-us": "en", "en-001": "en",
}


def normalize_help_locale(locale, center=None):
    normalized = LOCALE_ALIASES.get((locale or "").lower())
    if center and normalized and not center.locale_supported(normalized):
        return center.default_locale
    return normalized or (center.default_locale if center else "es")


def center_for_request(request, slug=None):
    qs = HelpCenter.objects.filter(active=True).select_related("brand")
    if slug:
        return qs.filter(slug=slug).first()
    host = request.get_host().split(":", 1)[0].lower()
    for center in qs:
        if host in [d.lower() for d in (center.domains or [])]:
            return center
    return None


def center_for_ticket(ticket):
    center = HelpCenter.objects.filter(active=True, service__iexact=ticket.service or "").first()
    if not center and ticket.brand_id:
        center = HelpCenter.objects.filter(active=True, brand_id=ticket.brand_id).first()
    return center


def public_base_url(center):
    if center and center.slug == "ecomfax":
        return getattr(settings, "ECOMFAX_HELP_PUBLIC_URL", "").rstrip("/")
    if center and center.slug == "recordia":
        return getattr(settings, "RECORDIA_HELP_PUBLIC_URL", "").rstrip("/")
    return (getattr(settings, "TICKETFLOW_PUBLIC_URL", "") or "").rstrip("/")


class PublicTicketIntakeService:
    @staticmethod
    def _digest(value):
        key = settings.SECRET_KEY.encode("utf-8")
        return hmac.new(key, (value or "").strip().lower().encode("utf-8"), hashlib.sha256).hexdigest()

    @staticmethod
    def _brand_for(center):
        if center.brand_id:
            return center.brand
        setting_name = "PUBLIC_ECOMFAX_BRAND_NAME" if center.slug == "ecomfax" else "PUBLIC_RECORDIA_BRAND_NAME"
        configured = getattr(settings, setting_name, "")
        return Brand.objects.filter(name__iexact=configured).first()

    @staticmethod
    def _check_rate(center, remote_ip, email):
        now = timezone.now()
        ip_hash = PublicTicketIntakeService._digest(remote_ip or "unknown")
        email_hash = PublicTicketIntakeService._digest(email)
        ip_cutoff = now - timedelta(minutes=getattr(settings, "PUBLIC_TICKET_IP_WINDOW_MINUTES", 10))
        email_cutoff = now - timedelta(minutes=getattr(settings, "PUBLIC_TICKET_EMAIL_WINDOW_MINUTES", 60))
        if PublicTicketAttempt.objects.filter(center=center, ip_hash=ip_hash, created_at__gte=ip_cutoff).count() >= getattr(settings, "PUBLIC_TICKET_IP_LIMIT", 5):
            raise APIValidationError("rate_limited", "Has enviado demasiadas solicitudes. Espera unos minutos antes de volver a intentarlo.", status=429)
        if PublicTicketAttempt.objects.filter(center=center, email_hash=email_hash, created_at__gte=email_cutoff).count() >= getattr(settings, "PUBLIC_TICKET_EMAIL_LIMIT", 3):
            raise APIValidationError("rate_limited", "Has enviado demasiadas solicitudes. Espera unos minutos antes de volver a intentarlo.", status=429)
        return ip_hash, email_hash

    @staticmethod
    @transaction.atomic
    def create(center, cleaned_data, files, remote_ip):
        files = AttachmentService.validate_files(files)
        email = cleaned_data["email"].strip().lower()
        ip_hash, email_hash = PublicTicketIntakeService._check_rate(center, remote_ip, email)
        attempt = PublicTicketAttempt.objects.create(
            center=center, ip_hash=ip_hash, email_hash=email_hash, accepted=False,
        )

        brand = PublicTicketIntakeService._brand_for(center)
        if not brand:
            raise APIValidationError(
                "portal_brand_missing",
                "El portal no tiene configurado el buzón de soporte. Inténtalo más tarde.",
                status=503,
            )
        requester = EmailIngestionService.get_or_create_requester(email, "")
        phone = cleaned_data["phone"].strip()
        if not requester.phone and phone:
            requester.phone = phone
            requester.save(update_fields=["phone", "updated_at"])

        ticket = TicketService.create_ticket(requester, {
            "subject": cleaned_data["subject"].strip(),
            "description": cleaned_data["description"].strip(),
            "content": cleaned_data["description"].strip(),
            "status": "open",
            "requester_id": requester.id,
            "brand": brand,
            "channel": "web",
            "service": center.service,
            "language": cleaned_data.get("locale") or center.default_locale,
            "via_channel": "web",
            "is_public": True,
        })
        comment = ticket.comment_set.order_by("created_at", "id").first()
        for uploaded in files:
            AttachmentService.save(uploaded, ticket, requester, comment=comment)
        attempt.accepted = True
        attempt.save(update_fields=["accepted"])
        return ticket
