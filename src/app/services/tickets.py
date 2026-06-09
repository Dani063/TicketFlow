from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from app.api import APIValidationError
from app.models import Brand, Comment, Group, Ticket, TicketEvent, TicketTag, User
from app.permissions import can_update_ticket, is_agent
from app.services.assignment import AssignmentService
from app.services.automations import AutomationService
from app.services.comments import CommentService
from app.services.metrics import record_metric
from app.services.notifications import NotificationService
from app.services.sla import SLAService


class TicketService:
    STATUS_VALUES = {"open", "pending", "closed", "resolved"}
    PRIORITY_VALUES = {"low", "normal", "high", "urgent"}
    EVENT_FIELDS = [
        ("status", "status"),
        ("priority", "priority"),
        ("subject", "subject"),
        ("assignee_id", "assignee_id"),
        ("group_id", "assigned_group_id"),
    ]

    @staticmethod
    def create_or_update_from_post(actor, post_data, ticket_id=None):
        payload = TicketService._payload_from_post(actor, post_data)
        if ticket_id:
            return TicketService.update_ticket(actor, ticket_id, payload)
        return TicketService.create_ticket(actor, payload)

    @staticmethod
    def _payload_from_post(actor, post_data):
        subject = (post_data.get("subject") or "").strip()
        if not subject:
            raise APIValidationError("subject_required", "El asunto es obligatorio.", status=400)

        status = (post_data.get("status") or "open").strip().lower()
        if status not in TicketService.STATUS_VALUES:
            status = "open"

        priority = (post_data.get("prioridad") or "").strip().lower() or None
        if priority not in TicketService.PRIORITY_VALUES:
            priority = None

        empresa_name = (post_data.get("empresa") or "").strip()
        brand = Brand.objects.get_or_create(name=empresa_name)[0] if empresa_name else None
        grupo_id = TicketService._optional_int(post_data.get("grupo"))
        asignado_id = TicketService._optional_int(post_data.get("asignado"))
        solicitante_id = TicketService._optional_int(post_data.get("solicitante")) or actor.id

        content = ((post_data.get("content") or post_data.get("message")) or "").strip()
        due_at_raw = (post_data.get("due_at") or "").strip()
        due_at = parse_datetime(due_at_raw.replace("T", " ")) if due_at_raw else None

        return {
            "subject": subject,
            "description": content or "",
            "content": content,
            "status": status,
            "priority": priority,
            "requester_id": solicitante_id,
            "assignee_id": asignado_id,
            "assigned_group_id": grupo_id,
            "brand": brand,
            "type": post_data.get("tipo") or None,
            "channel": post_data.get("canal") or None,
            "service": post_data.get("servicio") or None,
            "language": post_data.get("idioma") or None,
            "category": post_data.get("categoria") or None,
            "due_at": due_at,
            "security_related": post_data.get("security_related") in ("1", "true", "on"),
            "monitoring": post_data.get("monitoring") in ("1", "true", "on"),
            "approval_status": (post_data.get("approval_status") or "").strip() or None,
            "resolution_type": (post_data.get("resolution_type") or "").strip() or None,
            "required_tasks": (post_data.get("required_tasks") or "").strip() or None,
            "ccs_ids": TicketService._get_list(post_data, "ccs"),
            "tags": TicketService._get_list(post_data, "tags"),
            "is_public": str((post_data.get("is_public") or "true")).lower() in ("true", "1", "yes", "on"),
        }

    @staticmethod
    @transaction.atomic
    def create_ticket(actor, payload):
        requester_id = payload.get("requester_id") or actor.id
        if not is_agent(actor) and requester_id != actor.id:
            raise APIValidationError("invalid_requester", "No puedes crear tickets para otro solicitante.", status=403)
        if not User.objects.filter(id=requester_id, is_active=True).exists():
            raise APIValidationError("requester_not_found", "Solicitante no encontrado.", status=400)

        assignee_id = payload.get("assignee_id")
        if not assignee_id:
            assignee = AssignmentService.choose_assignee(
                group_id=payload.get("assigned_group_id"),
                service=payload.get("service"),
                channel=payload.get("channel"),
            )
            assignee_id = assignee.id if assignee else None

        now = timezone.now()
        ticket = Ticket.objects.create(
            subject=payload["subject"],
            description=payload.get("description") or "",
            status=payload.get("status") or "open",
            priority=payload.get("priority"),
            requester_id=requester_id,
            assignee_id=assignee_id,
            assigned_group_id=payload.get("assigned_group_id"),
            created_by_id=actor.id,
            brand=payload.get("brand"),
            type=payload.get("type"),
            channel=payload.get("channel"),
            service=payload.get("service"),
            language=payload.get("language"),
            category=payload.get("category"),
            due_at=payload.get("due_at"),
            security_related=payload.get("security_related"),
            monitoring=payload.get("monitoring"),
            approval_status=payload.get("approval_status"),
            resolution_type=payload.get("resolution_type"),
            required_tasks=payload.get("required_tasks"),
            email_message_id=payload.get("email_message_id"),
            email_conversation_id=payload.get("email_conversation_id"),
            closed_at=now if payload.get("status") == "closed" else None,
        )
        TicketService._set_m2m(ticket, payload.get("ccs_ids") or [], payload.get("tags") or [])
        SLAService.apply_policy(ticket)
        TicketEvent.objects.create(
            ticket=ticket,
            actor=actor,
            field_name="created",
            old_value=None,
            new_value=ticket.status,
            created_at=now,
        )
        if payload.get("content"):
            CommentService.add_comment(
                ticket,
                actor,
                payload.get("content"),
                is_public=payload.get("is_public", True),
                via_channel=payload.get("via_channel"),
                email_message_id=payload.get("email_message_id"),
                notify=False,
            )
        NotificationService.notify_ticket_users(ticket, f"Nuevo ticket #{ticket.id}: {ticket.subject}", actor=actor)
        AutomationService.run_for_ticket(ticket, actor=actor, event="ticket_created", event_key=f"ticket_created:{ticket.id}")
        record_metric("tickets.created", labels={"ticket_id": ticket.id})
        return ticket

    @staticmethod
    @transaction.atomic
    def update_ticket(actor, ticket_id, payload):
        ticket = Ticket.objects.select_for_update().get(id=ticket_id)
        if not can_update_ticket(actor, ticket):
            raise APIValidationError("forbidden", "No puedes actualizar este ticket.", status=403)

        old_values = {
            event_name: getattr(ticket, model_field)
            for event_name, model_field in TicketService.EVENT_FIELDS
        }
        old_tags = set(ticket.tags.values_list("name", flat=True))

        for field in (
            "subject",
            "description",
            "status",
            "priority",
            "brand",
            "type",
            "channel",
            "service",
            "language",
            "category",
            "due_at",
            "security_related",
            "monitoring",
            "approval_status",
            "resolution_type",
            "required_tasks",
        ):
            setattr(ticket, field, payload.get(field))
        ticket.assignee_id = payload.get("assignee_id")
        ticket.assigned_group_id = payload.get("assigned_group_id")
        if ticket.status == "closed" and not ticket.closed_at:
            ticket.closed_at = timezone.now()
        ticket.save()
        TicketService._set_m2m(ticket, payload.get("ccs_ids") or [], payload.get("tags") or [])

        now = timezone.now()
        for event_name, model_field in TicketService.EVENT_FIELDS:
            new_value = getattr(ticket, model_field)
            old_value = old_values[event_name]
            if old_value != new_value:
                TicketEvent.objects.create(
                    ticket=ticket,
                    actor=actor,
                    field_name=event_name,
                    old_value=str(old_value) if old_value is not None else None,
                    new_value=str(new_value) if new_value is not None else None,
                    created_at=now,
                )

        new_tags = set(ticket.tags.values_list("name", flat=True))
        if old_tags != new_tags:
            TicketEvent.objects.create(
                ticket=ticket,
                actor=actor,
                field_name="tags",
                old_value=",".join(sorted(old_tags)),
                new_value=",".join(sorted(new_tags)),
                created_at=now,
            )

        SLAService.apply_policy(ticket)
        NotificationService.notify_ticket_users(ticket, f"Ticket #{ticket.id} actualizado", actor=actor)
        if payload.get("content"):
            CommentService.add_comment(
                ticket,
                actor,
                payload.get("content"),
                is_public=payload.get("is_public", True),
                notify=False,
            )
        AutomationService.run_for_ticket(ticket, actor=actor, event="ticket_updated")
        record_metric("tickets.updated", labels={"ticket_id": ticket.id})
        return ticket

    @staticmethod
    def _set_m2m(ticket, ccs_ids, tags_in):
        if ccs_ids:
            ticket.ccs.set([int(user_id) for user_id in ccs_ids if str(user_id).isdigit()])
        else:
            ticket.ccs.clear()

        ticket.tags.clear()
        for tag in tags_in:
            if str(tag).isdigit():
                try:
                    ticket.tags.add(TicketTag.objects.get(id=tag))
                except TicketTag.DoesNotExist:
                    pass
            else:
                tag_name = str(tag).strip()
                if tag_name:
                    tag_obj, _ = TicketTag.objects.get_or_create(name=tag_name)
                    ticket.tags.add(tag_obj)

    @staticmethod
    def _optional_int(value):
        try:
            return int(value) if value not in (None, "") else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _get_list(data, key):
        if hasattr(data, "getlist"):
            return data.getlist(key)
        value = data.get(key)
        if value is None:
            return []
        return value if isinstance(value, list) else [value]
