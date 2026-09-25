from django.db import transaction
from django.utils import timezone

from app.api import APIValidationError
from app.models import Comment, Ticket, TicketEvent
from app.permissions import is_agent
from app.services.comments import CommentService
from app.services.metrics import record_metric
from app.services.notifications import NotificationService


class MergeService:
    @staticmethod
    def _post_merge_comment(ticket, actor, message):
        """Publica el comentario que el agente ha revisado en el modal de fusión.

        `message` es {content, html_body, is_public} o None. Vacío equivale a no
        dejar comentario: el agente puede borrar el texto propuesto a propósito.
        """
        if not message:
            return None
        content = (message.get("content") or "").strip()
        if not content:
            return None
        comment, _ = CommentService.add_comment(
            ticket,
            actor,
            content,
            html_body=message.get("html_body") or "",
            is_public=bool(message.get("is_public")),
            # La fusión ya avisa a los implicados con su propia notificación.
            notify=False,
        )
        return comment

    @staticmethod
    @transaction.atomic
    def merge_ticket(source_id, target_id, actor, source_message=None, target_message=None):
        if not is_agent(actor):
            raise APIValidationError("forbidden", "Permiso denegado", status=403)
        if int(source_id) == int(target_id):
            raise APIValidationError("self_merge", "No puedes fusionar un ticket consigo mismo", status=400)

        source = Ticket.objects.select_for_update().get(pk=source_id)
        target = Ticket.objects.select_for_update().get(pk=target_id)
        if source.merged_into_id:
            raise APIValidationError("already_merged", "Este ticket ya esta fusionado", status=400)
        if target.merged_into_id or target.is_deleted:
            raise APIValidationError("invalid_target", "El ticket destino no esta disponible", status=400)

        Comment.objects.filter(ticket=source).update(ticket=target)
        TicketEvent.objects.filter(ticket=source).update(ticket=target)
        # Después de mover la conversación: el comentario de cierre se queda en
        # el origen (y le llega a su solicitante si es público) y el del destino
        # no se duplica al trasladar los comentarios.
        MergeService._post_merge_comment(target, actor, target_message)
        MergeService._post_merge_comment(source, actor, source_message)
        source.merged_into = target
        source.status = "closed"
        source.closed_at = timezone.now()
        source.save(update_fields=["merged_into", "status", "closed_at"])

        TicketEvent.objects.create(
            ticket=target,
            actor=actor,
            field_name="merge",
            old_value=None,
            new_value=f"#{source.zendesk_id or source.id}",
            created_at=timezone.now(),
        )
        NotificationService.notify_ticket_users(target, f"Ticket #{source.zendesk_id or source.id} fusionado aqui", actor=actor)
        record_metric("tickets.merged", labels={"source_id": source.id, "target_id": target.id})
        return target

    @staticmethod
    @transaction.atomic
    def bulk_merge(source_ids, target_id, actor, source_message=None, target_message=None):
        if not is_agent(actor):
            raise APIValidationError("forbidden", "Permiso denegado", status=403)
        target = Ticket.objects.select_for_update().get(pk=target_id, merged_into__isnull=True, is_deleted=False)
        merged_count = 0
        for source_id in source_ids:
            if int(source_id) == int(target.id):
                continue
            source = Ticket.objects.select_for_update().filter(
                pk=source_id,
                merged_into__isnull=True,
                is_deleted=False,
            ).first()
            if not source:
                continue
            Comment.objects.filter(ticket=source).update(ticket=target)
            TicketEvent.objects.filter(ticket=source).update(ticket=target)
            # Cada origen recibe el mismo comentario de cierre (y su solicitante
            # el correo, si es público); ver merge_ticket.
            MergeService._post_merge_comment(source, actor, source_message)
            source.merged_into = target
            source.status = "closed"
            source.closed_at = timezone.now()
            source.save(update_fields=["merged_into", "status", "closed_at"])
            TicketEvent.objects.create(
                ticket=target,
                actor=actor,
                field_name="merge",
                old_value=None,
                new_value=f"#{source.zendesk_id or source.id}",
                created_at=timezone.now(),
            )
            merged_count += 1

        if merged_count:
            MergeService._post_merge_comment(target, actor, target_message)
            NotificationService.notify_ticket_users(target, f"{merged_count} ticket(s) fusionados aqui", actor=actor)
            record_metric("tickets.bulk_merged", merged_count, labels={"target_id": target.id})
        return target, merged_count
