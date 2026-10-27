import html as htmllib
import logging
import re
from collections import Counter
from datetime import timedelta

from django.core.files.uploadedfile import SimpleUploadedFile
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import transaction
from django.utils import timezone
from django.utils.html import strip_tags

from app.api import APIValidationError
from app.models import Attachment, Comment, InboundEmailLog, Role, Ticket, TicketEvent, User
from app.permissions import is_agent
from app.sanitizers import resolve_inline_cids, sanitize_email_html
from app.services.attachments import AttachmentService
from app.services.metrics import record_metric
from app.services.notifications import NotificationService
from app.services.sla import SLAService
from app.services.tickets import TicketService

logger = logging.getLogger(__name__)

TICKET_REF_RE = re.compile(r"\[Ticket #(\d+)\]", re.IGNORECASE)
STRIP_PREFIX_RE = re.compile(r"^(re|fw|fwd|rv|resp):\s*", re.IGNORECASE)
TICKET_TAG_RE = re.compile(r"\[ticket\s*#\d+\]", re.IGNORECASE)
BODY_RE = re.compile(r"<body[^>]*>(.*?)</body>", re.DOTALL | re.IGNORECASE)


def exact_match(queryset, field, value):
    """Primer objeto cuyo ``field`` es exactamente ``value``.

    Los ids de Graph distinguen mayúsculas, pero las columnas que los guardan en
    MySQL usan una colación que no: un id nuevo coincidía en la consulta con otro
    antiguo que solo difería en una letra (son base64 consecutivos, así que pasa
    a menudo) y el correo se daba por duplicado sin dejar rastro. Así se perdió
    correo en PRO el 01/10 y el 02/10/2026. La consulta acota; la igualdad se
    comprueba aquí.
    """
    if not value:
        return None
    for obj in queryset.filter(**{field: value}):
        if getattr(obj, field) == value:
            return obj
    return None


class EmailIngestionService:
    @staticmethod
    def is_auto_generated(message):
        """Detecta auto-respuestas/correo automatizado por cabeceras RFC 3834 y
        afines, o remitente que es un buzón propio. Estos correos siguen creando
        ticket, pero NUNCA reciben confirmación automática (anti-bucles)."""
        headers = {
            (h.get("name") or "").strip().lower(): (h.get("value") or "").strip().lower()
            for h in (message.get("internetMessageHeaders") or [])
        }
        auto_submitted = headers.get("auto-submitted", "")
        if auto_submitted and auto_submitted != "no":
            return True
        suppress = headers.get("x-auto-response-suppress", "")
        if any(token in suppress for token in ("all", "oof", "autoreply")):
            return True
        if headers.get("precedence", "") in ("bulk", "junk", "auto_reply", "list"):
            return True
        if "x-autoreply" in headers or "x-autorespond" in headers or "list-id" in headers:
            return True

        sender_email = (message.get("from", {}).get("emailAddress", {}).get("address") or "").strip().lower()
        if sender_email:
            from app.models import Brand
            own = {
                e.lower()
                for e in Brand.objects.exclude(support_email="").values_list("support_email", flat=True)
            }
            if sender_email in own:
                return True
        return False

    @staticmethod
    def process_message(message, brand, attachments=None):
        msg_id = message["id"]
        conversation_id = message.get("conversationId") or ""
        log, created = InboundEmailLog.objects.get_or_create(
            brand=brand,
            message_id=msg_id,
            defaults={
                "conversation_id": conversation_id,
                "status": InboundEmailLog.STATUS_RECEIVED,
                "payload": message,
            },
        )
        if not created and log.status in (InboundEmailLog.STATUS_PROCESSED, InboundEmailLog.STATUS_DUPLICATE):
            log.status = InboundEmailLog.STATUS_DUPLICATE
            log.result = "skip_dup"
            log.processed_at = timezone.now()
            log.save(update_fields=["status", "result", "processed_at"])
            return "skip_dup"

        log.status = InboundEmailLog.STATUS_RECEIVED
        log.conversation_id = conversation_id
        log.payload = message
        log.error = None
        log.save(update_fields=["status", "conversation_id", "payload", "error"])

        try:
            result, ticket = EmailIngestionService._process_message(
                message, brand, attachments=attachments,
            )
        except Exception as exc:
            log.status = InboundEmailLog.STATUS_FAILED
            log.error = str(exc)
            log.processed_at = timezone.now()
            log.save(update_fields=["status", "error", "processed_at"])
            record_metric("email.failed", labels={"brand_id": brand.id if brand else None})
            logger.exception("inbound_email_failed", extra={"message_id": msg_id})
            raise

        log.status = (
            InboundEmailLog.STATUS_DUPLICATE
            if result == "skip_dup"
            else InboundEmailLog.STATUS_PROCESSED
        )
        log.result = result
        log.ticket = ticket
        log.processed_at = timezone.now()
        log.save(update_fields=["status", "result", "ticket", "processed_at"])
        return result

    @staticmethod
    def _process_message(message, brand, attachments=None, suppress_confirmation=False):
        """Ingiere un mensaje ya descargado. Idempotente por ``email_message_id``.

        ``suppress_confirmation`` lo usa el backfill: un ticket recuperado días
        después no debe enviar al cliente un «hemos recibido tu correo».
        """
        msg_id = message["id"]
        conversation_id = message.get("conversationId") or ""
        subject = message.get("subject") or "(sin asunto)"
        sender = message.get("from", {}).get("emailAddress", {})
        sender_email = sender.get("address", "").strip().lower()
        sender_name = sender.get("name", "")
        text_body, html_body = EmailIngestionService.extract_body(message)
        inline_html_body = html_body
        if html_body:
            html_body = sanitize_email_html(html_body)

        if not sender_email:
            return "skip_no_sender", None

        # Graph can expose malformed From addresses (including URL fragments).
        # Retrying one of those forever would hold the mailbox delta cursor and
        # prevent later, valid replies from reaching their tickets.
        try:
            if len(sender_email) > User._meta.get_field("email").max_length:
                raise ValidationError("sender exceeds user email field")
            validate_email(sender_email)
        except ValidationError:
            logger.warning(
                "inbound_invalid_sender",
                extra={"message_id": msg_id, "sender_length": len(sender_email)},
            )
            record_metric("email.inbound.invalid_sender", labels={"brand_id": brand.id if brand else None})
            return "skip_invalid_sender", None

        requester = EmailIngestionService.get_or_create_requester(sender_email, sender_name)
        existing_comment = exact_match(
            Comment.objects.select_related("ticket"), "email_message_id", msg_id,
        )
        if existing_comment:
            EmailIngestionService._store_attachments(
                existing_comment.ticket, existing_comment, requester, attachments,
                html_body=inline_html_body,
            )
            return "skip_dup", existing_comment.ticket

        existing_ticket = exact_match(Ticket.objects.all(), "email_message_id", msg_id)
        if existing_ticket:
            comment = exact_match(
                Comment.objects.filter(ticket=existing_ticket), "email_message_id", msg_id,
            )
            if attachments and comment is None:
                comment = EmailIngestionService._create_attachment_only_comment(
                    existing_ticket, requester, msg_id,
                )
            EmailIngestionService._store_attachments(
                existing_ticket, comment, requester, attachments,
                html_body=inline_html_body,
            )
            return "skip_dup", existing_ticket

        ticket = EmailIngestionService.find_ticket_for_message(subject, conversation_id, brand, requester=requester)

        if ticket:
            ticket.ccs.add(*EmailIngestionService.cc_users(message, ticket.requester))
            comment = EmailIngestionService._add_email_comment(
                ticket=ticket,
                actor=requester,
                content=text_body or html_body,
                html_body=html_body,
                email_message_id=msg_id,
            )
            EmailIngestionService._store_attachments(
                ticket, comment, requester, attachments,
                html_body=inline_html_body,
            )
            return "comment_added", ticket

        ticket = TicketService.create_ticket(
            requester,
            {
                "subject": subject,
                "description": text_body or html_body or "",
                "content": text_body or html_body or "",
                "html_body": html_body,
                "status": "open",
                "priority": None,
                "requester_id": requester.id,
                "ccs_ids": [user.id for user in EmailIngestionService.cc_users(message, requester)],
                "brand": brand,
                "channel": "email",
                "email_message_id": msg_id,
                "email_conversation_id": conversation_id or None,
                "via_channel": "email",
                "is_public": True,
                "suppress_requester_email": (
                    suppress_confirmation or EmailIngestionService.is_auto_generated(message)
                ),
            },
        )
        comment = exact_match(Comment.objects.filter(ticket=ticket), "email_message_id", msg_id)
        if attachments and comment is None:
            comment = EmailIngestionService._create_attachment_only_comment(
                ticket, requester, msg_id,
            )
        EmailIngestionService._store_attachments(ticket, comment, requester, attachments, html_body=inline_html_body)
        return "ticket_created", ticket

    @staticmethod
    def cc_users(message, requester):
        """Recupera copias válidas sin incluir al solicitante ni buzones propios."""
        from app.models import Brand

        excluded = {
            email.strip().lower()
            for email in Brand.objects.exclude(support_email="").values_list("support_email", flat=True)
        }
        if requester:
            excluded.add(requester.email.strip().lower())
        users = []
        for recipient in message.get("ccRecipients") or []:
            if not isinstance(recipient, dict):
                continue
            address = recipient.get("emailAddress")
            if not isinstance(address, dict) or not isinstance(address.get("address"), str):
                continue
            email = address["address"].strip().lower()
            try:
                User._meta.get_field("email").clean(email, None)
            except ValidationError:
                continue
            if email in excluded:
                continue
            excluded.add(email)
            name = address.get("name")
            name = name.strip() if isinstance(name, str) else ""
            name = name[:User._meta.get_field("name").max_length]
            users.append(EmailIngestionService.get_or_create_requester(email, name))
        return users

    @staticmethod
    def _create_attachment_only_comment(ticket, actor, email_message_id):
        return Comment.objects.create(
            ticket=ticket,
            user=actor,
            content="",
            via_channel="email",
            created_at=timezone.now(),
            is_public=True,
            email_message_id=email_message_id,
        )

    @staticmethod
    def _store_attachments(ticket, comment, actor, attachments, *, html_body=None):
        """Guarda adjuntos descargados y permite reanudar sin duplicarlos."""
        if not attachments or comment is None:
            return []

        existing = Counter(
            Attachment.objects.filter(comment=comment, ticket=ticket).values_list("original_name", "size", "content_id", "is_inline")
        )
        encountered = Counter()
        stored = []
        for item in attachments:
            uploaded = SimpleUploadedFile(
                item.get("name") or "archivo",
                item.get("content") or b"",
                content_type=item.get("content_type") or "application/octet-stream",
            )
            content_id = str(item.get('content_id') or '').strip().strip('<>')
            is_inline = bool(item.get('is_inline'))
            key = (AttachmentService.original_filename(uploaded.name), uploaded.size, content_id, is_inline)
            encountered[key] += 1
            if existing[key] >= encountered[key]:
                continue
            try:
                attachment = AttachmentService.save(uploaded, ticket, actor, comment=comment)
                attachment.content_id = content_id
                attachment.is_inline = is_inline
                attachment.save(update_fields=['content_id', 'is_inline'])
                stored.append(attachment)
            except APIValidationError as exc:
                if exc.status >= 500:
                    raise
                logger.warning(
                    "inbound_attachment_rejected",
                    extra={
                        "ticket_id": ticket.id,
                        "message_id": comment.email_message_id,
                        "attachment_name": uploaded.name,
                        "error_code": exc.code,
                    },
                )
        if html_body:
            previews = {
                attachment.content_id: preview
                for attachment in Attachment.objects.filter(comment=comment, ticket=ticket, is_inline=True).exclude(content_id='')
                if (preview := AttachmentService.metadata(attachment)['preview_url'])
            }
            rendered = resolve_inline_cids(html_body, previews)
            if rendered != comment.html_body:
                comment.html_body = rendered
                comment.save(update_fields=['html_body'])
        return stored

    @staticmethod
    def get_or_create_requester(email, name):
        email = email.strip().lower()
        user, created = User.objects.get_or_create(
            email=email,
            defaults={"name": name.strip() if name else email.split("@")[0]},
        )
        if created:
            user.set_unusable_password()
            try:
                role, _ = Role.objects.get_or_create(role_name="End user")
                user.role = role
            except Exception:
                pass
            user.save()
        return user

    @staticmethod
    def find_ticket_for_message(subject, conversation_id, brand, requester=None):
        if conversation_id:
            ticket = exact_match(
                Ticket.objects.filter(brand=brand), "email_conversation_id", conversation_id,
            )
            if ticket:
                return ticket

        match = TICKET_REF_RE.search(subject or "")
        if match:
            number = int(match.group(1))
            for lookup in ({"id": number, "brand": brand}, {"zendesk_id": number, "brand": brand}):
                try:
                    return Ticket.objects.get(**lookup)
                except Ticket.DoesNotExist:
                    pass

        if requester and not conversation_id:
            normalized = EmailIngestionService.normalize_subject(subject or "")
            if len(normalized) >= 4:
                cutoff = timezone.now() - timedelta(days=7)
                return (
                    Ticket.objects.filter(
                        brand=brand,
                        requester=requester,
                        status__in=("open", "pending"),
                        created_at__gte=cutoff,
                        email_conversation_id__isnull=True,
                    )
                    .filter(subject__icontains=normalized[:60])
                    .order_by("-created_at")
                    .first()
                )
        return None

    @staticmethod
    def normalize_subject(subject):
        value = TICKET_TAG_RE.sub("", subject).strip()
        while True:
            cleaned = STRIP_PREFIX_RE.sub("", value).strip()
            if cleaned == value:
                break
            value = cleaned
        return value.lower()

    @staticmethod
    def extract_body(message):
        body = message.get("body", {})
        raw = body.get("content", "")
        if body.get("contentType", "html").lower() == "html":
            match = BODY_RE.search(raw)
            html_inner = match.group(1).strip() if match else raw
            return EmailIngestionService.html_to_text(html_inner), html_inner
        return htmllib.unescape(raw).strip(), ""

    @staticmethod
    def html_to_text(html_inner):
        text = re.sub(r"<br\s*/?>", "\n", html_inner, flags=re.IGNORECASE)
        text = re.sub(r"</(?:p|div|tr|li|h[1-6])>", "\n", text, flags=re.IGNORECASE)
        return htmllib.unescape(strip_tags(text)).strip()

    @staticmethod
    @transaction.atomic
    def _add_email_comment(ticket, actor, content, html_body="", email_message_id=None):
        if not is_agent(actor) and ticket.requester_id != actor.id and not ticket.ccs.filter(id=actor.id).exists():
            ticket.ccs.add(actor)

        now = timezone.now()
        comment = Comment.objects.create(
            ticket=ticket,
            user=actor,
            content=content or html_body or "",
            html_body=html_body or None,
            via_channel="email",
            created_at=now,
            is_public=True,
            email_message_id=email_message_id,
        )

        if ticket.status in ("pending", "resolved"):
            old_status = ticket.status
            ticket.status = "open"
            ticket.updated_at = now
            ticket.save(update_fields=["status", "updated_at"])
            TicketEvent.objects.create(
                ticket=ticket,
                actor=actor,
                field_name="status",
                old_value=old_status,
                new_value="open",
                created_at=now,
            )
        else:
            ticket.updated_at = now
            ticket.save(update_fields=["updated_at"])

        SLAService.record_first_response(ticket, actor, comment_is_public=True)
        NotificationService.notify_ticket_users(ticket, f"Nuevo email en ticket #{ticket.id}", actor=actor)
        record_metric("email.processed", labels={"ticket_id": ticket.id})
        return comment
