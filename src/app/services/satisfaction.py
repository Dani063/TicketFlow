"""Encuesta de satisfacción (CSAT) nativa: oferta, voto y catálogo de motivos.

Replica la mecánica de Zendesk, que es la que ya asume el modelo:

  · La OFERTA de encuesta es una fila SatisfactionRating con score='offered'.
  · El voto del cliente ACTUALIZA esa misma fila a 'good'/'bad'.

De ahí que las métricas de la app filtren por score__in=('good','bad'): quien no
contesta no cuenta como insatisfecho, no entra en el denominador del CSAT.
"""

import logging
import secrets
from datetime import timedelta

from django.conf import settings
from django.db.models import Exists, OuterRef
from django.utils import timezone

from app.models import SatisfactionRating, SatisfactionReason, Ticket, TicketEvent
from app.services.email_outbound import OutboundEmailService
from app.services.metrics import record_metric

logger = logging.getLogger(__name__)

# Estados desde los que se encuesta. 'closed' entra porque un agente puede cerrar
# sin pasar por 'resolved' y esos tickets no se encuestarían nunca.
TERMINAL_STATUSES = ("resolved", "closed")


class SatisfactionService:

    # ------------------------------------------------------------------
    # Oferta
    # ------------------------------------------------------------------

    @staticmethod
    def _new_token():
        # 32 bytes urlsafe (43 caracteres): no adivinable por fuerza bruta, así que
        # el enlace público no necesita rate limiting propio.
        return secrets.token_urlsafe(32)

    @staticmethod
    def pending_tickets(now=None, limit=None):
        """Tickets con la resolución suficientemente asentada y sin encuesta del ciclo actual.

        «Del ciclo actual» = sin valoración nativa creada después del resolved_at
        vigente, para que una reapertura seguida de nueva resolución vuelva a ser
        encuestable, como en Zendesk.
        """
        now = now or timezone.now()
        cutoff = now - timedelta(hours=getattr(settings, "SATISFACTION_SURVEY_DELAY_HOURS", 24))
        already_offered = SatisfactionRating.objects.filter(
            ticket=OuterRef("pk"),
            source=SatisfactionRating.SOURCE_NATIVE,
            created_at__gte=OuterRef("resolved_at"),
        )
        qs = (
            Ticket.objects
            .filter(
                status__in=TERMINAL_STATUSES,
                resolved_at__isnull=False,
                resolved_at__lte=cutoff,
                is_deleted=False,
                merged_into__isnull=True,
            )
            .exclude(Exists(already_offered))
            .select_related("requester", "assignee", "brand")
            .order_by("resolved_at")
        )
        return qs[:limit] if limit else qs

    @staticmethod
    def offer(ticket, now=None):
        """Crea la oferta y encola el email. Devuelve el rating, o None si no procede.

        La comprobación de envío se hace ANTES de crear la fila: si el email no va a
        salir (kill switch, sin destinatario, sin plantilla…) no queremos dejar una
        oferta que nadie puede responder contaminando la tasa de respuesta.
        """
        # Sin URL pública los enlaces de voto saldrían vacíos: un correo pidiendo
        # valoración con botones muertos es peor que no enviarlo.
        from app.models import HelpCenter
        center = HelpCenter.objects.filter(active=True, service__iexact=ticket.service or "").first()
        if center and center.slug == "ecomfax":
            public_url = getattr(settings, "ECOMFAX_HELP_PUBLIC_URL", "")
        elif center and center.slug == "recordia":
            public_url = getattr(settings, "RECORDIA_HELP_PUBLIC_URL", "")
        else:
            public_url = getattr(settings, "TICKETFLOW_PUBLIC_URL", "")
        if not (public_url or "").strip():
            logger.warning("satisfaction_offer_skipped_no_public_url", extra={"ticket_id": ticket.id})
            record_metric("satisfaction.offer_skipped", labels={"ticket_id": ticket.id, "reason": "no_public_url"})
            return None

        blocked = OutboundEmailService.blocking_reason(ticket)
        if blocked:
            record_metric("satisfaction.offer_skipped", labels={"ticket_id": ticket.id, "reason": blocked})
            return None

        now = now or timezone.now()
        ttl_days = getattr(settings, "SATISFACTION_SURVEY_TTL_DAYS", 30)
        rating = SatisfactionRating.objects.create(
            ticket=ticket,
            score="offered",
            source=SatisfactionRating.SOURCE_NATIVE,
            token=SatisfactionService._new_token(),
            requester=ticket.requester if ticket.requester_id else None,
            assignee=ticket.assignee if ticket.assignee_id else None,
            offered_at=now,
            expires_at=now + timedelta(days=ttl_days),
            created_at=now,
            updated_at=now,
        )
        OutboundEmailService.queue_satisfaction_survey(ticket, rating)
        record_metric("satisfaction.offered", labels={"ticket_id": ticket.id})
        return rating

    @staticmethod
    def offer_pending_surveys():
        """Barrido periódico. Devuelve el número de encuestas ofrecidas."""
        if not getattr(settings, "SATISFACTION_SURVEY_ENABLED", False):
            return 0

        limit = getattr(settings, "SATISFACTION_SURVEY_BATCH", 200)
        tickets = list(SatisfactionService.pending_tickets(limit=limit))
        offered = 0
        for ticket in tickets:
            try:
                if SatisfactionService.offer(ticket):
                    offered += 1
            except Exception:
                # Un ticket problemático no puede tumbar el barrido completo.
                logger.exception("satisfaction_offer_failed", extra={"ticket_id": ticket.id})
        if len(tickets) >= limit:
            # Sin esto, un arranque en frío parecería «todo encuestado» cuando en
            # realidad quedan tickets fuera del lote.
            logger.warning("satisfaction_offer_batch_full", extra={"limit": limit})
        return offered

    # ------------------------------------------------------------------
    # Voto
    # ------------------------------------------------------------------

    @staticmethod
    def reasons_for(language="es"):
        """Motivos activos del idioma, con caída a español si no hay traducción."""
        reasons = list(SatisfactionReason.objects.filter(active=True, language=language))
        if not reasons and language != "es":
            reasons = list(SatisfactionReason.objects.filter(active=True, language="es"))
        return reasons

    @staticmethod
    def rating_for_token(token):
        if not token:
            return None
        return (
            SatisfactionRating.objects
            .select_related("ticket", "ticket__brand", "ticket__assignee", "reason_choice")
            .filter(token=token, source=SatisfactionRating.SOURCE_NATIVE)
            .first()
        )

    @staticmethod
    def record_vote(rating, score, comment="", reason=None, now=None):
        """Registra (o rectifica) el voto sobre la fila de la oferta."""
        if score not in ("good", "bad"):
            raise ValueError(f"score inválido: {score!r}")
        now = now or timezone.now()

        previous_score = rating.score
        rating.score = score
        rating.comment = ((comment or "").strip()[:2000]) or None
        # El motivo solo tiene sentido en un voto negativo; si rectifica a positivo
        # hay que limpiarlo, o quedaría un «bien» con motivo de queja colgado.
        rating.reason_choice = reason if score == "bad" else None
        rating.responded_at = now
        rating.updated_at = now
        # Atribución al asignado en el MOMENTO DEL VOTO, no en el de la oferta: es la
        # semántica de Zendesk y el motivo de que el rating lleve su propio assignee.
        if rating.ticket_id and rating.ticket and rating.ticket.assignee_id:
            rating.assignee_id = rating.ticket.assignee_id
        rating.save(update_fields=[
            "score", "comment", "reason_choice", "responded_at", "updated_at", "assignee",
        ])
        if rating.ticket_id and previous_score != score:
            TicketEvent.objects.create(
                ticket_id=rating.ticket_id,
                actor=rating.requester,
                field_name="satisfaction_score",
                old_value=previous_score,
                new_value=score,
                created_at=now,
            )
        record_metric("satisfaction.voted", labels={
            "score": score,
            "ticket_id": rating.ticket_id or 0,
        })
        return rating
