import logging

from django.db.models import Count
from django.utils import timezone

from app.models import AssignmentRule, Ticket, TicketEvent, User
from app.permissions import agent_user_filter
from app.services.metrics import record_metric

logger = logging.getLogger(__name__)


class AssignmentService:
    @staticmethod
    def agent_queryset():
        return User.objects.filter(is_active=True).filter(agent_user_filter())

    @staticmethod
    def matching_rules(group_id=None, service=None, channel=None):
        rules = AssignmentRule.objects.filter(active=True).prefetch_related("members__user").order_by("name")
        matches = []
        for rule in rules:
            if rule.group_id and str(rule.group_id) != str(group_id or ""):
                continue
            if rule.service and rule.service != service:
                continue
            if rule.channel and rule.channel != channel:
                continue
            score = int(bool(rule.group_id)) + int(bool(rule.service)) + int(bool(rule.channel))
            matches.append((score, rule))
        matches.sort(key=lambda item: (-item[0], item[1].name))
        return [rule for _, rule in matches]

    @staticmethod
    def choose_assignee(group_id=None, service=None, channel=None):
        rules = AssignmentService.matching_rules(group_id=group_id, service=service, channel=channel)
        for rule in rules:
            assignee = AssignmentService._choose_from_rule(rule)
            if assignee:
                return assignee

        fallback = list(AssignmentService.agent_queryset().order_by("id")[:50])
        return AssignmentService._choose_balanced(fallback)

    @staticmethod
    def _choose_from_rule(rule):
        members = [
            member
            for member in rule.members.all()
            if member.active and member.user and member.user.is_active
        ]
        users = [member.user for member in members]
        if not users:
            return None

        counts = AssignmentService._open_ticket_counts([user.id for user in users])
        candidates = []
        for member in members:
            current = counts.get(member.user_id, 0)
            if member.capacity is not None and current >= member.capacity:
                continue
            weight = max(member.weight or 1, 1)
            candidates.append((current / weight, current, member.user.id, member.user))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (item[0], item[1], item[2]))
        return candidates[0][3]

    @staticmethod
    def _choose_balanced(users):
        if not users:
            return None
        counts = AssignmentService._open_ticket_counts([user.id for user in users])
        return sorted(users, key=lambda user: (counts.get(user.id, 0), user.id))[0]

    @staticmethod
    def _open_ticket_counts(user_ids):
        if not user_ids:
            return {}
        return dict(
            Ticket.objects.filter(assignee_id__in=user_ids, is_deleted=False)
            .exclude(status__in=["closed", "resolved"])
            .values("assignee_id")
            .annotate(n=Count("id"))
            .values_list("assignee_id", "n")
        )

    @staticmethod
    def auto_assign_unassigned():
        unassigned = Ticket.objects.filter(
            assignee__isnull=True,
            is_deleted=False,
            merged_into__isnull=True,
        ).order_by("created_at")

        assigned_count = 0
        now = timezone.now()
        for ticket in unassigned:
            assignee = AssignmentService.choose_assignee(
                group_id=ticket.assigned_group_id,
                service=ticket.service,
                channel=ticket.channel,
            )
            if not assignee:
                break
            ticket.assignee = assignee
            ticket.save(update_fields=["assignee"])
            TicketEvent.objects.create(
                ticket=ticket,
                actor=None,
                field_name="assignee_id",
                old_value=None,
                new_value=str(assignee.id),
                created_at=now,
            )
            assigned_count += 1

        if assigned_count:
            record_metric("tickets.auto_assigned", assigned_count)
        logger.info("auto_assign_unassigned", extra={"assigned_count": assigned_count})
        return assigned_count
