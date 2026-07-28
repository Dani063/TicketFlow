"""Envío de email saliente por marca: Amazon SES (rol IAM) o Microsoft 365 (Graph).

El envío real ocurre en la tarea Celery send_outbound_email; aquí se decide si
procede enviar (anti-bucles), se renderiza la plantilla y se registra todo en
OutboundEmailLog (queued/sent/failed/suppressed).
"""

import logging
import re

from django.conf import settings
from django.db import transaction

from app.models import Brand, OutboundEmailLog
from app.services.email_templates import ResponseTemplateService
from app.services.metrics import record_metric

logger = logging.getLogger(__name__)

NO_REPLY_RE = re.compile(r"(no-?reply|do-?not-?reply|mailer-daemon|postmaster|bounces?@)", re.I)


class OutboundEmailService:
    TEMPLATE_TICKET_CREATED = "ticket_created"
    TEMPLATE_SATISFACTION_SURVEY = "satisfaction_survey"

    @staticmethod
    def _own_mailboxes():
        return {
            email.lower()
            for email in Brand.objects.exclude(support_email="").values_list("support_email", flat=True)
        }

    @staticmethod
    def _suppress(ticket, brand, to_email, template_key, reason):
        log = OutboundEmailLog.objects.create(
            brand=brand,
            ticket=ticket,
            template_key=template_key,
            provider=brand.mailbox_type if brand else "",
            to_email=to_email or "",
            status=OutboundEmailLog.STATUS_SUPPRESSED,
            result=reason,
        )
        record_metric("email.outbound.suppressed", labels={"ticket_id": ticket.id, "reason": reason})
        return log

    @staticmethod
    def _recipient(ticket):
        """(brand remitente, email del solicitante) — destinatario de toda plantilla."""
        requester = ticket.requester if ticket.requester_id else None
        return (
            ticket.brand if ticket.brand_id else None,
            (requester.email if requester else "") or "",
        )

    @staticmethod
    def blocking_reason(ticket):
        """Motivo por el que un envío a este ticket no saldría, o None.

        Permite decidir ANTES de crear efectos secundarios (p.ej. la oferta de
        encuesta) si merece la pena intentarlo.
        """
        brand, to_email = OutboundEmailService._recipient(ticket)
        return OutboundEmailService._delivery_block(brand, to_email)

    @staticmethod
    def _delivery_block(brand, to_email):
        """Motivo por el que NO se debe enviar, o None si se puede. Común a todas
        las plantillas: el kill switch, destinatario válido, marca remitente y las
        dos protecciones anti-bucle."""
        if not getattr(settings, "OUTBOUND_EMAIL_ENABLED", False):
            return "disabled"
        if not to_email or "@" not in to_email:
            return "no_recipient"
        if brand is None or not brand.support_email:
            return "no_brand"
        if to_email.lower() in OutboundEmailService._own_mailboxes():
            # Nunca auto-responder a un buzón propio: bucle garantizado.
            return "own_mailbox"
        if NO_REPLY_RE.search(to_email):
            return "noreply_pattern"
        return None

    @staticmethod
    def queue_ticket_confirmation(ticket):
        """Encola la confirmación de creación al solicitante. Idempotente por
        (plantilla, ticket, destinatario); las supresiones quedan registradas."""
        return OutboundEmailService._queue(
            ticket,
            OutboundEmailService.TEMPLATE_TICKET_CREATED,
            dedup_suffix=str(ticket.id),
            context_builder=ResponseTemplateService.context_for_ticket,
        )

    @staticmethod
    def queue_satisfaction_survey(ticket, rating):
        """Encola la encuesta de satisfacción al solicitante.

        Idempotente por rating, no por ticket: un ticket reabierto y vuelto a
        resolver genera una oferta nueva y por tanto puede encuestarse otra vez,
        como hacía Zendesk.
        """
        return OutboundEmailService._queue(
            ticket,
            OutboundEmailService.TEMPLATE_SATISFACTION_SURVEY,
            dedup_suffix=str(rating.id),
            context_builder=lambda tkt: ResponseTemplateService.context_for_survey(tkt, rating),
        )

    @staticmethod
    def _queue(ticket, template_key, dedup_suffix, context_builder):
        brand, to_email = OutboundEmailService._recipient(ticket)

        blocked = OutboundEmailService._delivery_block(brand, to_email)
        if blocked:
            return OutboundEmailService._suppress(ticket, brand, to_email, template_key, blocked)

        language = ResponseTemplateService.pick_language(ticket)
        template = ResponseTemplateService.resolve(template_key, brand=brand, language=language)
        if template is None:
            return OutboundEmailService._suppress(ticket, brand, to_email, template_key, "no_template")

        rendered = ResponseTemplateService.render(template, context_builder(ticket))
        dedup_key = f"{template_key}:{dedup_suffix}:{to_email.lower()}"[:190]
        log, created = OutboundEmailLog.objects.get_or_create(
            dedup_key=dedup_key,
            defaults={
                "brand": brand,
                "ticket": ticket,
                "template_key": template_key,
                "provider": brand.mailbox_type,
                "to_email": to_email,
                "subject": rendered["subject"],
                "payload": {
                    "body_html": rendered["body_html"],
                    "body_text": rendered["body_text"],
                    "language": language,
                },
                "status": OutboundEmailLog.STATUS_QUEUED,
            },
        )
        if not created:
            return log

        log_id = log.id
        ticket_id = ticket.id

        def _dispatch():
            try:
                from app.tasks import send_outbound_email  # import local: evita ciclo tasks->services
                send_outbound_email.delay(log_id)
            except Exception:
                # Broker caído => la operación de negocio (crear ticket, ofrecer
                # encuesta) sigue adelante; el log queda 'queued' y es re-procesable
                # (resend manual o tarea de barrido futura).
                logger.exception("outbound_dispatch_failed", extra={"log_id": log_id, "ticket_id": ticket_id})
                record_metric("email.outbound.dispatch_failed", labels={"log_id": log_id})

        transaction.on_commit(_dispatch)
        record_metric("email.outbound.queued", labels={"ticket_id": ticket_id})
        return log

    # ------------------------------------------------------------------
    # Entrega (llamada desde la tarea Celery)
    # ------------------------------------------------------------------

    @staticmethod
    def deliver(log):
        """Envía el email del log. Devuelve el message id del proveedor (o None)."""
        if log.brand is None or not log.brand.support_email:
            raise RuntimeError(f"OutboundEmailLog #{log.id} sin marca/buzón remitente")
        if log.brand.mailbox_type == "ses":
            return OutboundEmailService._send_via_ses(log)
        return OutboundEmailService._send_via_graph(log)

    @staticmethod
    def _send_via_ses(log):
        # SESv2 con boto3: autentica con el rol IAM del pod (como SQS/SSM),
        # sin credenciales SMTP que gestionar/rotar; devuelve MessageId y
        # permite la cabecera Auto-Submitted (Graph no).
        import boto3

        brand = log.brand
        payload = log.payload or {}
        client = boto3.client("sesv2", region_name=getattr(settings, "AWS_SES_REGION", "eu-west-1"))
        kwargs = {
            "FromEmailAddress": f"{brand.from_name or brand.name} <{brand.support_email}>",
            "Destination": {"ToAddresses": [log.to_email]},
            "Content": {
                "Simple": {
                    "Subject": {"Data": log.subject, "Charset": "UTF-8"},
                    "Body": {
                        "Html": {"Data": payload.get("body_html") or "", "Charset": "UTF-8"},
                        "Text": {"Data": payload.get("body_text") or "", "Charset": "UTF-8"},
                    },
                    "Headers": [
                        {"Name": "Auto-Submitted", "Value": "auto-generated"},
                        {"Name": "X-Auto-Response-Suppress", "Value": "All"},
                    ],
                }
            },
        }
        config_set = getattr(settings, "SES_CONFIGURATION_SET", "")
        if config_set:
            kwargs["ConfigurationSetName"] = config_set
        response = client.send_email(**kwargs)
        return response.get("MessageId")

    @staticmethod
    def _send_via_graph(log):
        # Graph sendMail con las credenciales MSAL ya usadas para leer buzones.
        # Requiere el permiso de aplicación Mail.Send (ver docs/email-integration-setup.md).
        # Las cabeceras custom de Graph deben empezar por X-, así que la
        # protección anti-bucle aquí es X-Auto-Response-Suppress (Exchange la honra).
        import requests

        from app.services.msgraph import GRAPH_BASE, get_graph_token

        brand = log.brand
        payload = log.payload or {}
        token = get_graph_token()
        message = {
            "subject": log.subject,
            "body": {"contentType": "HTML", "content": payload.get("body_html") or ""},
            "toRecipients": [{"emailAddress": {"address": log.to_email}}],
            "internetMessageHeaders": [
                {"name": "X-Auto-Response-Suppress", "value": "All"},
            ],
        }
        response = requests.post(
            f"{GRAPH_BASE}/users/{brand.support_email}/sendMail",
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
            json={"message": message, "saveToSentItems": True},
            timeout=30,
        )
        response.raise_for_status()
        return None  # Graph devuelve 202 sin message id; la copia queda en Enviados
