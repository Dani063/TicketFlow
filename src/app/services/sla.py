import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Case, DateTimeField, F, Q, When
from django.db.models.functions import Least
from django.utils import timezone

from app.constants import PRIORITY_ESCALATION, SLA_AT_RISK_WINDOW_MINUTES
from app.models import SLAPolicy, Ticket, TicketEvent
from app.permissions import is_agent
from app.services.metrics import record_metric
from app.services.notifications import NotificationService

logger = logging.getLogger(__name__)


class SLAService:
    # Tickets exentos de SLA: alarmas/notificaciones automáticas de monitorización
    # (AWS CloudWatch, etc.). No hay compromiso de respuesta sobre ellas.
    @staticmethod
    def is_sla_exempt(ticket):
        if getattr(ticket, "monitoring", False):
            return True
        subject = (ticket.subject or "").lstrip()
        return subject.upper().startswith("ALARM:")

    @staticmethod
    def find_policy(ticket):
        org_id = ticket.requester.organization_id if ticket.requester_id and ticket.requester else None

        qs = SLAPolicy.objects.filter(active=True)
        qs = qs.filter(Q(priority__isnull=True) | Q(priority="") | Q(priority=ticket.priority))
        qs = qs.filter(Q(service__isnull=True) | Q(service="") | Q(service=ticket.service))
        qs = qs.filter(Q(brand__isnull=True) | Q(brand=ticket.brand))
        qs = qs.filter(Q(organization__isnull=True) | Q(organization_id=org_id))
        qs = qs.filter(Q(assigned_group__isnull=True) | Q(assigned_group=ticket.assigned_group))

        best = None
        best_score = -1
        for policy in qs:
            score = (
                int(bool(policy.priority))
                + int(bool(policy.service))
                + int(bool(policy.brand_id))
                + int(bool(policy.organization_id))
                + int(bool(policy.assigned_group_id))
            )
            if score > best_score:
                best = policy
                best_score = score
        return best

    @staticmethod
    def annotate_urgency(qs):
        """Anota sla_next_due = próximo vencimiento (primera respuesta o resolución).

        Las guardas Case son obligatorias: LEAST() en MySQL devuelve NULL si
        cualquiera de los argumentos es NULL.
        """
        return qs.annotate(
            sla_next_due=Case(
                When(first_response_due_at__isnull=True, then=F("resolution_due_at")),
                When(resolution_due_at__isnull=True, then=F("first_response_due_at")),
                default=Least("first_response_due_at", "resolution_due_at"),
                output_field=DateTimeField(),
            )
        )

    @staticmethod
    def apply_policy(ticket, save=True):
        # Las alarmas/notificaciones automáticas no llevan SLA.
        if SLAService.is_sla_exempt(ticket):
            return None
        policy = SLAService.find_policy(ticket)
        if not policy:
            return None

        base = ticket.created_at or timezone.now()
        update_fields = []
        # Solo armamos el vencimiento de primera respuesta si aun no se ha
        # respondido. Sin esta guarda, cada update re-fijaria el due desde
        # created_at (ya en el pasado) y lo marcaria como incumplido.
        if policy.first_response_minutes and not ticket.first_responded_at:
            ticket.first_response_due_at = base + timedelta(minutes=policy.first_response_minutes)
            update_fields.append("first_response_due_at")
        if policy.resolution_minutes and ticket.status not in ("closed", "resolved"):
            ticket.resolution_due_at = base + timedelta(minutes=policy.resolution_minutes)
            ticket.due_at = ticket.resolution_due_at
            update_fields.extend(["resolution_due_at", "due_at"])
        if save and update_fields:
            ticket.save(update_fields=update_fields)
        return policy

    @staticmethod
    def record_first_response(ticket, actor, comment_is_public=True):
        if not comment_is_public or not is_agent(actor):
            return False
        if ticket.first_responded_at:
            return False

        now = timezone.now()
        had_due = bool(ticket.first_response_due_at)
        ticket.first_responded_at = now
        update_fields = ["first_responded_at"]
        if had_due:
            # El due se limpia, así que el cumplimiento debe persistirse ahora:
            # después no es computable.
            ticket.first_response_met = now <= ticket.first_response_due_at
            ticket.first_response_due_at = None
            update_fields.extend(["first_response_met", "first_response_due_at"])
        ticket.save(update_fields=update_fields)
        TicketEvent.objects.create(
            ticket=ticket,
            actor=actor,
            field_name="first_response",
            old_value="due" if had_due else None,
            new_value="completed",
            created_at=now,
        )
        return True

    @staticmethod
    def mark_breaches(now=None):
        now = now or timezone.now()
        breached = []
        qs = Ticket.objects.select_related("requester", "assignee").filter(
            is_deleted=False,
            merged_into__isnull=True,
            sla_breached_at__isnull=True,
        ).filter(
            Q(first_response_due_at__isnull=False, first_response_due_at__lt=now)
            | Q(resolution_due_at__isnull=False, resolution_due_at__lt=now)
        ).exclude(status__in=("closed", "resolved"))

        with transaction.atomic():
            for ticket in qs.select_for_update():
                ticket.sla_breached_at = now
                update_fields = ["sla_breached_at"]

                # Escalado: subir prioridad para que el incumplimiento reordene la cola.
                old_priority = ticket.priority
                new_priority = PRIORITY_ESCALATION.get(old_priority, old_priority)
                if new_priority != old_priority:
                    ticket.priority = new_priority
                    ticket.sla_escalated_at = now
                    update_fields.extend(["priority", "sla_escalated_at"])
                    TicketEvent.objects.create(
                        ticket=ticket,
                        actor=None,
                        field_name="priority",
                        old_value=old_priority,
                        new_value=new_priority,
                        created_at=now,
                    )
                # No re-aplicar la política tras el escalado: recomputaría los
                # vencimientos desde created_at (ya en el pasado).
                ticket.save(update_fields=update_fields)
                TicketEvent.objects.create(
                    ticket=ticket,
                    actor=None,
                    field_name="sla_breach",
                    old_value=None,
                    new_value=now.isoformat(),
                    created_at=now,
                )
                NotificationService.notify_ticket_users(ticket, f"SLA breach on ticket #{ticket.id}")
                if ticket.assigned_group_id:
                    NotificationService.notify_group(
                        ticket,
                        f"SLA incumplido en ticket #{ticket.id}: {ticket.subject}",
                        ticket.assigned_group,
                        exclude_ids={ticket.assignee_id} if ticket.assignee_id else (),
                    )
                record_metric("sla.breach", labels={"ticket_id": ticket.id})
                breached.append(ticket.id)

        logger.info("sla_breach_scan", extra={"breached_count": len(breached)})
        return breached

    @staticmethod
    def notify_at_risk(window_minutes=SLA_AT_RISK_WINDOW_MINUTES, now=None):
        """Avisa al asignado y a su grupo cuando el próximo vencimiento de SLA
        está dentro de la ventana. Idempotente vía sla_risk_notified_at."""
        now = now or timezone.now()
        deadline = now + timedelta(minutes=window_minutes)
        qs = SLAService.annotate_urgency(
            Ticket.objects.select_related("assignee", "assigned_group").filter(
                is_deleted=False,
                merged_into__isnull=True,
                sla_breached_at__isnull=True,
                sla_risk_notified_at__isnull=True,
            ).exclude(status__in=("closed", "resolved"))
        ).filter(sla_next_due__gt=now, sla_next_due__lte=deadline)

        notified = []
        with transaction.atomic():
            for ticket in qs.select_for_update():
                ticket.sla_risk_notified_at = now
                ticket.save(update_fields=["sla_risk_notified_at"])
                TicketEvent.objects.create(
                    ticket=ticket,
                    actor=None,
                    field_name="sla_at_risk",
                    old_value=None,
                    new_value=now.isoformat(),
                    created_at=now,
                )
                message = f"SLA en riesgo: el ticket #{ticket.id} vence en menos de {window_minutes} min"
                if ticket.assignee_id:
                    NotificationService.notify_users(ticket, message, [ticket.assignee])
                if ticket.assigned_group_id:
                    NotificationService.notify_group(
                        ticket,
                        message,
                        ticket.assigned_group,
                        exclude_ids={ticket.assignee_id} if ticket.assignee_id else (),
                    )
                record_metric("sla.at_risk", labels={"ticket_id": ticket.id})
                notified.append(ticket.id)

        logger.info("sla_at_risk_scan", extra={"at_risk_count": len(notified)})
        return notified
