from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from app.api import APIValidationError
from app.constants import CHANNEL_VALUES, LEGACY_CHANNEL_MAP, LEGACY_TYPE_MAP, TICKET_TYPE_VALUES
from app.models import Brand, Comment, Group, ProductLine, Ticket, TicketEvent, TicketTag, User
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
    PRODUCT_SERVICE_ALIASES = {
        "recordia": "recordia",
        "speech analytics": "speech-analytics",
        "speech_analytics": "speech-analytics",
        "speech-analytics": "speech-analytics",
        "identia": "identia",
        "ecomfax": "ecomfax",
        "ecomfaxpro": "ecomfax",
        "agentia365": "agentia365",
        "agentia_365": "agentia365",
        "agentia-365": "agentia365",
    }
    EVENT_FIELDS = [
        ("status", "status"),
        ("priority", "priority"),
        ("subject", "subject"),
        ("assignee_id", "assignee_id"),
        ("group_id", "assigned_group_id"),
        ("product_line_id", "product_line_id"),
        ("type", "type"),
        ("problem_id", "problem_id"),
    ]

    @staticmethod
    def normalize_type(raw):
        """Acepta valores canónicos y legacy del formulario; inválidos -> None."""
        value = (raw or "").strip().lower()
        if not value:
            return None
        return LEGACY_TYPE_MAP.get(value) or (value if value in TICKET_TYPE_VALUES else None)

    @staticmethod
    def normalize_channel(raw):
        value = (raw or "").strip().lower()
        if not value:
            return None
        return LEGACY_CHANNEL_MAP.get(value) or (value if value in CHANNEL_VALUES else None)

    @staticmethod
    def product_from_service(service):
        """Clasifica canales automáticos cuando el servicio es inequívoco."""
        value = (service or "").strip().lower()
        if not value:
            return None
        code = TicketService.PRODUCT_SERVICE_ALIASES.get(value, "otros-servicios")
        return ProductLine.objects.filter(code=code, active=True).first()

    @staticmethod
    def create_or_update_from_post(actor, post_data, ticket_id=None):
        payload = TicketService._payload_from_post(actor, post_data)
        if ticket_id:
            return TicketService.update_ticket(actor, ticket_id, payload)
        # El formulario web es el único caller; canal por defecto solo al crear
        # (en update machacaría el canal real, p. ej. "email").
        payload["channel"] = payload.get("channel") or "web"
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
        brand = None
        if empresa_name:
            # Lookup estricto: el select es de vocabulario cerrado; get_or_create
            # creaba marcas fantasma ante cualquier valor inesperado.
            brand = Brand.objects.filter(name=empresa_name).first()
            if brand is None:
                raise APIValidationError("brand_not_found", f"Empresa desconocida: {empresa_name}", status=400)
        product_line_id = TicketService._optional_int(post_data.get("producto"))
        if not product_line_id:
            raise APIValidationError(
                "product_required", "Selecciona un producto o servicio.", status=400
            )
        product_line = ProductLine.objects.filter(id=product_line_id, active=True).first()
        if product_line is None:
            raise APIValidationError(
                "product_not_found", "El producto o servicio seleccionado no está disponible.", status=400
            )
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
            "product_line": product_line,
            "type": TicketService.normalize_type(post_data.get("tipo")),
            "channel": TicketService.normalize_channel(post_data.get("canal")),
            "problem_id": TicketService._optional_int(post_data.get("problem_id")),
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
    def _validate_problem_link(payload, ticket_id=None):
        """Devuelve el problem_id validado (o None). Solo incidentes pueden vincularse a un problema."""
        problem_id = payload.get("problem_id")
        if not problem_id:
            return None
        if payload.get("type") != "incident":
            raise APIValidationError(
                "invalid_problem_link", "Solo una incidencia puede vincularse a un problema.", status=400
            )
        if ticket_id and int(problem_id) == int(ticket_id):
            raise APIValidationError("invalid_problem_link", "Un ticket no puede vincularse a sí mismo.", status=400)
        target = Ticket.objects.filter(
            id=problem_id, is_deleted=False, merged_into__isnull=True
        ).only("id", "type").first()
        if target is None or target.type != "problem":
            raise APIValidationError(
                "invalid_problem_link", f"El ticket #{problem_id} no existe o no es de tipo problema.", status=400
            )
        return target.id

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

        problem_id = TicketService._validate_problem_link(payload)

        now = timezone.now()
        product_line = payload.get("product_line") or TicketService.product_from_service(payload.get("service"))
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
            product_line=product_line,
            type=payload.get("type"),
            problem_id=problem_id,
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
            resolved_at=now if payload.get("status") == "resolved" else None,
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
        if ticket.product_line_id:
            TicketEvent.objects.create(
                ticket=ticket,
                actor=actor,
                field_name="product_line_id",
                old_value=None,
                new_value=str(ticket.product_line_id),
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
        if not payload.get("suppress_requester_email"):
            # Import local: email_outbound importa modelos y este módulo se importa
            # desde tasks vía email_ingestion (evitar ciclos).
            from app.services.email_outbound import OutboundEmailService
            OutboundEmailService.queue_ticket_confirmation(ticket)

        from django.conf import settings as dj_settings
        if getattr(dj_settings, "AI_CLASSIFICATION_ENABLED", False):
            ticket_id = ticket.id

            def _dispatch_ai():
                try:
                    # Import local: tasks.py importa este módulo vía email_ingestion (ciclo)
                    from app.tasks import classify_ticket_ai
                    classify_ticket_ai.delay(ticket_id)
                except Exception:
                    # Broker caído => el ticket se crea igual, sin clasificar
                    import logging
                    logging.getLogger(__name__).exception("ai_dispatch_failed", extra={"ticket_id": ticket_id})
                    record_metric("ai.dispatch_failed", labels={"ticket_id": ticket_id})

            transaction.on_commit(_dispatch_ai)
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
            "product_line",
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
        ticket.problem_id = TicketService._validate_problem_link(payload, ticket_id=ticket.id)
        ticket.assignee_id = payload.get("assignee_id")
        ticket.assigned_group_id = payload.get("assigned_group_id")
        if ticket.status == "closed" and not ticket.closed_at:
            ticket.closed_at = timezone.now()
        # Reloj del que cuelga la encuesta de satisfacción. Se refresca en CADA
        # entrada en 'resolved' (una reapertura y nueva resolución abre un ciclo de
        # encuesta nuevo); un cierre directo sin pasar por 'resolved' también cuenta
        # como resolución, o esos tickets no se encuestarían nunca.
        if old_values["status"] != "resolved" and ticket.status == "resolved":
            ticket.resolved_at = timezone.now()
        elif ticket.status == "closed" and not ticket.resolved_at:
            ticket.resolved_at = ticket.closed_at or timezone.now()
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
