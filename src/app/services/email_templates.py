"""Resolución y renderizado de plantillas de respuesta al cliente (ES/EN, por marca)."""

from django.template import engines
from django.utils.html import escape, linebreaks, strip_tags

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
        # Una plantilla editada en el panel puede no conocer el aviso de cierre;
        # entonces va al final para que el cliente no se quede sin él.
        if context.get("closing_notice_html") and "closing_notice" not in template.body_html:
            rendered_html += context["closing_notice_html"]
        if context.get("closing_notice_text") and "closing_notice" not in body_text_source:
            rendered_text += "\n\n" + context["closing_notice_text"]
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
        from app.models import HelpCenter
        center = HelpCenter.objects.filter(active=True, service__iexact=ticket.service or "").first()
        if not center and ticket.brand_id:
            center = HelpCenter.objects.filter(active=True, brand_id=ticket.brand_id).first()
        if center and center.slug == "ecomfax":
            base_url = (getattr(dj_settings, "ECOMFAX_HELP_PUBLIC_URL", "") or "").rstrip("/")
        elif center and center.slug == "recordia":
            base_url = (getattr(dj_settings, "RECORDIA_HELP_PUBLIC_URL", "") or "").rstrip("/")
        else:
            base_url = (getattr(dj_settings, "TICKETFLOW_PUBLIC_URL", "") or "").rstrip("/")
        locale = ResponseTemplateService.pick_language(ticket)
        return {
            "ticket_id": ticket.id,
            "subject": ticket.subject,
            "requester_name": ticket.requester.name if ticket.requester_id and ticket.requester else "",
            "requester_email": ticket.requester.email if ticket.requester_id and ticket.requester else "",
            "brand_name": brand.name if brand else "",
            "support_email": brand.support_email if brand else "",
            "from_name": (brand.from_name or brand.name) if brand else "",
            # El cliente final no accede a la aplicacion interna. El correo le
            # devuelve al centro publico y la conversacion continua respondiendo.
            "ticket_url": f"{base_url}/help/{center.slug}/{locale}/" if base_url and center else "",
            "created_at": ticket.created_at.strftime("%d/%m/%Y %H:%M") if ticket.created_at else "",
        }

    CLOSING_NOTICE = {
        "es": (
            "Damos por resuelta tu solicitud. Si necesitas algo más, responde a este "
            "correo en las próximas {hours} horas y continuaremos en el mismo ticket. "
            "Pasado ese plazo se cerrará definitivamente y, si vuelves a escribirnos, "
            "abriremos uno nuevo."
        ),
        "en": (
            "We consider your request resolved. If you need anything else, reply to "
            "this email within the next {hours} hours and we will continue in the same "
            "ticket. After that it will be permanently closed and, if you write to us "
            "again, we will open a new one."
        ),
    }

    @staticmethod
    def closing_notice(ticket):
        """Aviso de cierre de on_hold (#21384) en el idioma del ticket: (html, texto)."""
        from app.services.ticket_status import on_hold_close_hours

        language = ResponseTemplateService.pick_language(ticket)
        notice = ResponseTemplateService.CLOSING_NOTICE.get(
            language, ResponseTemplateService.CLOSING_NOTICE["es"],
        ).format(hours=on_hold_close_hours())
        return f"<p>{escape(notice)}</p>", notice

    @staticmethod
    def context_for_on_hold(ticket):
        """Contexto del aviso suelto de cierre: el del ticket más el aviso."""
        context = ResponseTemplateService.context_for_ticket(ticket)
        notice_html, notice_text = ResponseTemplateService.closing_notice(ticket)
        context.update({"closing_notice_html": notice_html, "closing_notice_text": notice_text})
        return context

    @staticmethod
    def context_for_agent_reply(ticket, comment, closing_notice=False):
        """Contexto seguro para la respuesta pública escrita por un agente."""
        context = ResponseTemplateService.context_for_ticket(ticket)
        notice_html, notice_text = (
            ResponseTemplateService.closing_notice(ticket) if closing_notice else ("", "")
        )
        context.update({"closing_notice_html": notice_html, "closing_notice_text": notice_text})
        if comment.html_body:
            reply_html = comment.html_body
            reply_text = strip_tags(comment.html_body).strip()
        else:
            reply_text = (comment.content or "").strip()
            reply_html = str(linebreaks(escape(reply_text)))
        context.update({
            "agent_name": comment.user.name if comment.user_id and comment.user else "",
            "reply_html": reply_html,
            "reply_text": reply_text,
            "responded_at": comment.created_at.strftime("%d/%m/%Y %H:%M") if comment.created_at else "",
        })
        return context

    @staticmethod
    def context_for_survey(ticket, rating):
        """Contexto del ticket más los enlaces de voto de la encuesta.

        survey_url_good/bad llevan ?score= para que el cliente vote de un clic y
        solo confirme, como hacía Zendesk. survey_url es la variante neutra.
        Sigue siendo un dict de escalares (ver render()).
        """
        from django.conf import settings as dj_settings

        context = ResponseTemplateService.context_for_ticket(ticket)
        ticket_url = context.get("ticket_url", "")
        base_url = ticket_url.split("/help/", 1)[0] if "/help/" in ticket_url else (getattr(dj_settings, "TICKETFLOW_PUBLIC_URL", "") or "").rstrip("/")
        survey_url = f"{base_url}/satisfaction/{rating.token}/" if base_url and rating.token else ""
        context.update({
            "survey_url": survey_url,
            "survey_url_good": f"{survey_url}?score=good" if survey_url else "",
            "survey_url_bad": f"{survey_url}?score=bad" if survey_url else "",
        })
        return context
