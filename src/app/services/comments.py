from django.db import transaction
from django.utils import timezone

from app.api import APIValidationError
from app.models import Attachment, Comment, TicketEvent
from app.permissions import can_comment_ticket, can_update_ticket, is_agent
from app.sanitizers import sanitize_email_html
from app.services.metrics import record_metric
from app.services.notifications import NotificationService
from app.services.sla import SLAService


class CommentService:
    @staticmethod
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
    ):
        if not can_comment_ticket(actor, ticket):
            raise APIValidationError("forbidden", "No tienes acceso a este ticket.", status=403)

        content = (content or "").strip()
        html_body = (html_body or "").strip()
        if not content and not html_body:
            raise APIValidationError("empty_comment", "El contenido no puede estar vacio.", status=400)

        final_is_public = bool(is_public) if is_agent(actor) else True
        if html_body:
            html_body = sanitize_email_html(html_body)
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

        applied_status = ticket.status
        new_status = (new_status or "").lower()
        if new_status and new_status != ticket.status:
            if not can_update_ticket(actor, ticket):
                raise APIValidationError("forbidden_status_change", "No puedes cambiar el estado.", status=403)
            if new_status not in {"open", "pending", "resolved", "closed"}:
                raise APIValidationError("invalid_status", "Estado no valido.", status=400)
            prev = ticket.status
            ticket.status = new_status
            ticket.closed_at = now if new_status == "closed" else ticket.closed_at
            # Ver TicketService.update_ticket: resolved_at es el reloj de la encuesta
            # de satisfacción y se refresca en cada entrada en 'resolved'.
            if new_status == "resolved":
                ticket.resolved_at = now
            elif new_status == "closed" and not ticket.resolved_at:
                ticket.resolved_at = now
            ticket.updated_at = now
            ticket.save(update_fields=["status", "updated_at", "closed_at", "resolved_at"])
            TicketEvent.objects.create(
                ticket=ticket,
                actor=actor,
                field_name="status",
                old_value=prev,
                new_value=new_status,
                created_at=now,
            )
            applied_status = new_status
        else:
            ticket.updated_at = now
            ticket.save(update_fields=["updated_at"])

        if attachment_ids:
            Attachment.objects.filter(
                id__in=attachment_ids,
                ticket=ticket,
                comment__isnull=True,
            ).update(comment=comment)

        SLAService.record_first_response(ticket, actor, comment_is_public=final_is_public)

        if notify:
            NotificationService.notify_ticket_users(ticket, f"Nuevo comentario en ticket #{ticket.id}", actor=actor)
        record_metric("comments.created", labels={"ticket_id": ticket.id})
        return comment, applied_status
