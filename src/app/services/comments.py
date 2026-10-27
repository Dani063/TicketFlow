import logging

from django.db import transaction
from django.utils import timezone

from app.api import APIValidationError
from app.models import Attachment, Comment, Ticket
from app.permissions import can_comment_ticket, is_agent
from app.sanitizers import sanitize_email_html
from app.services.metrics import record_metric
from app.services.attachments import AttachmentService, retry_attachment_mutation
from app.services.notifications import NotificationService
from app.services.sla import SLAService
from app.services.ticket_status import apply_status


logger = logging.getLogger(__name__)


class CommentService:
    @staticmethod
    @retry_attachment_mutation
    @transaction.atomic
    def add_comment(
        ticket,
        actor,
        content,
        is_public=True,
        html_body="",
        new_status="",
        attachment_ids=None,
        via_channel=None,
        email_message_id=None,
        notify=True,
        send_requester_email=True,
        request_id=None,
    ):
        AttachmentService.lock_actor(actor)
        ticket = Ticket.objects.select_for_update().get(pk=ticket.pk)
        if not can_comment_ticket(actor, ticket):
            raise APIValidationError("forbidden", "No tienes acceso a este ticket.", status=403)

        if not isinstance(content, str) or not isinstance(html_body, str) or not isinstance(new_status, str) or type(is_public) is not bool:
            raise APIValidationError('invalid_comment', 'Contenido de comentario inválido.', status=400)
        content = content.strip()
        html_body = html_body.strip()
        attachment_ids = AttachmentService.parse_ids(attachment_ids)
        request_id = AttachmentService.parse_uuid(request_id, 'request_id')
        final_is_public = bool(is_public) if is_agent(actor) else True
        if html_body:
            html_body = sanitize_email_html(html_body)
        fingerprint = AttachmentService.fingerprint({
            'ticket': ticket.id, 'content': content, 'html_body': html_body,
            'is_public': final_is_public, 'new_status': new_status.lower(),
            'attachment_ids': attachment_ids, 'via_channel': via_channel,
            'email_message_id': email_message_id,
        })
        previous = AttachmentService.replay(actor, 'comment', request_id, fingerprint)
        if previous:
            if previous.comment_id is None:
                raise APIValidationError('comment_gone', 'El comentario ya no está disponible.', status=410)
            if not previous.comment.is_public and not is_agent(actor):
                raise APIValidationError('forbidden', 'No tienes acceso a este comentario.', status=403)
            return previous.comment, previous.result['new_status']
        attachments = AttachmentService.pending_for_publish(actor, attachment_ids, ticket=ticket)
        if not content and not html_body and not attachments:
            raise APIValidationError('empty_comment', 'El mensaje no puede estar vacío.', status=400)
        now = timezone.now()

        comment = Comment.objects.create(
            ticket=ticket,
            user=actor,
            content=content or html_body,
            html_body=html_body or None,
            via_channel=via_channel,
            created_at=now,
            is_public=final_is_public,
            email_message_id=email_message_id,
        )

        if not apply_status(ticket, actor, new_status, now):
            ticket.updated_at = now
            ticket.save(update_fields=["updated_at"])
        applied_status = ticket.status

        if attachments:
            Attachment.objects.filter(id__in=attachment_ids).update(comment=comment)

        SLAService.record_first_response(ticket, actor, comment_is_public=final_is_public)

        if send_requester_email and final_is_public and is_agent(actor) and not comment.is_inbound_email:
            try:
                from app.services.email_outbound import OutboundEmailService

                OutboundEmailService.queue_agent_reply(ticket, comment)
            except Exception:
                # El comentario ya es la operación principal: un error de plantilla
                # o de encolado no debe impedir que el agente responda al ticket.
                logger.exception(
                    "agent_reply_email_queue_failed",
                    extra={"ticket_id": ticket.id, "comment_id": comment.id},
                )
                record_metric(
                    "email.outbound.agent_reply_queue_failed",
                    labels={"ticket_id": ticket.id, "comment_id": comment.id},
                )

        if notify:
            NotificationService.notify_ticket_users(ticket, f"Nuevo comentario en ticket #{ticket.id}", actor=actor)
        AttachmentService.remember(actor, 'comment', request_id, fingerprint,
                                   ticket=ticket, comment=comment, result={'new_status': applied_status})
        record_metric("comments.created", labels={"ticket_id": ticket.id})
        return comment, applied_status
