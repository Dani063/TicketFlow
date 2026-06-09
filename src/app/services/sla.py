import logging
from datetime import timedelta

from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from app.models import SLAPolicy, Ticket, TicketEvent
from app.permissions import is_agent
from app.services.metrics import record_metric
from app.services.notifications import NotificationService

logger = logging.getLogger(__name__)


class SLAService:
    @staticmethod
    def find_policy(ticket):
        qs = SLAPolicy.objects.filter(active=True)
        qs = qs.filter(Q(priority__isnull=True) | Q(priority="") | Q(priority=ticket.priority))
        qs = qs.filter(Q(service__isnull=True) | Q(service="") | Q(service=ticket.service))
        qs = qs.filter(Q(assigned_group__isnull=True) | Q(assigned_group=ticket.assigned_group))

        best = None
        best_score = -1
        for policy in qs:
            score = int(bool(policy.priority)) + int(bool(policy.service)) + int(bool(policy.assigned_group_id))
            if score > best_score:
                best = policy
                best_score = score
        return best

    @staticmethod
    def apply_policy(ticket, save=True):
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
            ticket.first_response_due_at = None
            update_fields.append("first_response_due_at")
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
                ticket.save(update_fields=["sla_breached_at"])
                TicketEvent.objects.create(
                    ticket=ticket,
                    actor=None,
                    field_name="sla_breach",
                    old_value=None,
                    new_value=now.isoformat(),
                    created_at=now,
                )
                NotificationService.notify_ticket_users(ticket, f"SLA breach on ticket #{ticket.id}")
                record_metric("sla.breach", labels={"ticket_id": ticket.id})
                breached.append(ticket.id)

        logger.info("sla_breach_scan", extra={"breached_count": len(breached)})
        return breached
