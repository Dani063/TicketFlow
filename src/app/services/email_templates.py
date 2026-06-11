"""Resolución y renderizado de plantillas de respuesta al cliente (ES/EN, por marca)."""

from django.template import engines
from django.utils.html import strip_tags

from app.constants import normalize_language
from app.models import ResponseTemplate


class ResponseTemplateService:
    @staticmethod
    def pick_language(ticket):
        """Idioma de la plantilla: ticket.language -> brand.language -> 'es'.

        Ticket.language guarda valores libres heredados de Zendesk
        ('español'/'inglés'), de ahí la normalización.
        """
        return (
            normalize_language(ticket.language)
            or normalize_language(ticket.brand.language if ticket.brand_id and ticket.brand else None)
            or "es"
        )

    @staticmethod
    def resolve(key, brand=None, language="es"):
        """Cascada: (key, brand, lang) -> (key, global, lang) -> (key, brand, es) -> (key, global, es)."""
        candidates = [(brand, language), (None, language)]
        if language != "es":
            candidates += [(brand, "es"), (None, "es")]
        for cand_brand, cand_lang in candidates:
            tpl = ResponseTemplate.objects.filter(
                key=key, brand=cand_brand, language=cand_lang, active=True
            ).first()
            if tpl:
                return tpl
        return None

    @staticmethod
    def render(template, context):
        """Renderiza subject/body con el motor de Django sobre strings.

        El contexto debe ser un dict de ESCALARES (nunca instancias de modelo):
        impide el acceso a atributos arbitrarios desde una plantilla editada
        en el panel de administración.
        """
        engine = engines["django"]
        rendered_subject = engine.from_string(template.subject).render(context)
        rendered_html = engine.from_string(template.body_html).render(context)
        body_text_source = template.body_text or strip_tags(template.body_html)
        rendered_text = engine.from_string(body_text_source).render(context)
        # MySQL STRICT_TRANS_TABLES: subject de log/email limitado a 255
        return {
            "subject": rendered_subject[:255],
            "body_html": rendered_html,
            "body_text": rendered_text,
        }

    @staticmethod
    def context_for_ticket(ticket):
        """Contexto de escalares para las plantillas de un ticket."""
        from django.conf import settings as dj_settings

        brand = ticket.brand if ticket.brand_id else None
        base_url = (getattr(dj_settings, "TICKETFLOW_PUBLIC_URL", "") or "").rstrip("/")
        return {
            "ticket_id": ticket.id,
            "subject": ticket.subject,
            "requester_name": ticket.requester.name if ticket.requester_id and ticket.requester else "",
            "requester_email": ticket.requester.email if ticket.requester_id and ticket.requester else "",
            "brand_name": brand.name if brand else "",
            "support_email": brand.support_email if brand else "",
            "from_name": (brand.from_name or brand.name) if brand else "",
            "ticket_url": f"{base_url}/tickets/create/?id={ticket.id}" if base_url else "",
            "created_at": ticket.created_at.strftime("%d/%m/%Y %H:%M") if ticket.created_at else "",
        }
