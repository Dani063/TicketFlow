import logging

from django.db import IntegrityError
from django.utils import timezone

from app.models import AutomationExecution, AutomationRule, Group, TicketEvent, TicketTag, User
from app.services.notifications import NotificationService

logger = logging.getLogger(__name__)


class AutomationService:
    CONDITION_FIELDS = {"status", "priority", "service", "channel", "assigned_group"}

    @staticmethod
    def run_for_ticket(ticket, actor=None, event="ticket_updated", event_key=None):
        event_key = event_key or f"{event}:{ticket.id}:{timezone.now().isoformat()}"
        applied = []
        for rule in AutomationRule.objects.filter(active=True).order_by("priority", "name"):
            if not AutomationService._matches(ticket, rule.conditions or {}):
                continue
            try:
                _, created = AutomationExecution.objects.get_or_create(
                    rule=rule,
                    ticket=ticket,
                    event_key=event_key,
                )
            except IntegrityError:
                created = False
            if not created:
                continue
            AutomationService._apply_actions(ticket, actor, rule.actions or {})
            applied.append(rule.id)
        if applied:
            logger.info("automation_rules_applied", extra={"ticket_id": ticket.id, "rules": applied})
        return applied

    @staticmethod
    def _matches(ticket, conditions):
        for key, expected in conditions.items():
            if key == "tags":
                tag_names = set(ticket.tags.values_list("name", flat=True))
                expected_tags = set(expected if isinstance(expected, list) else [expected])
                if not expected_tags.issubset(tag_names):
                    return False
                continue
            if key == "assigned_group":
                actual = ticket.assigned_group_id
            elif key in AutomationService.CONDITION_FIELDS:
                actual = getattr(ticket, key)
            else:
                return False

            if isinstance(expected, list):
                if actual not in expected:
                    return False
            elif actual != expected:
                return False
        return True

    @staticmethod
    def _apply_actions(ticket, actor, actions):
        now = timezone.now()
        changed_fields = []

        for field in ("status", "priority"):
            if field in actions and getattr(ticket, field) != actions[field]:
                old = getattr(ticket, field)
                setattr(ticket, field, actions[field])
                changed_fields.append((field, old, actions[field]))

        if "assigned_group_id" in actions:
            group = Group.objects.filter(id=actions["assigned_group_id"]).first()
            if group and ticket.assigned_group_id != group.id:
                old = ticket.assigned_group_id
                ticket.assigned_group = group
                changed_fields.append(("group_id", old, group.id))

        if "assignee_id" in actions:
            user = User.objects.filter(id=actions["assignee_id"], is_active=True).first()
            if user and ticket.assignee_id != user.id:
                old = ticket.assignee_id
                ticket.assignee = user
                changed_fields.append(("assignee_id", old, user.id))

        if changed_fields:
            update_fields = ["updated_at"]
            for field, _, _ in changed_fields:
                if field == "group_id":
                    update_fields.append("assigned_group")
                elif field == "assignee_id":
                    update_fields.append("assignee")
                else:
                    update_fields.append(field)
            ticket.save(update_fields=list(dict.fromkeys(update_fields)))
            for field, old, new in changed_fields:
                TicketEvent.objects.create(
                    ticket=ticket,
                    actor=actor,
                    field_name=field,
                    old_value=str(old) if old is not None else None,
                    new_value=str(new) if new is not None else None,
                    created_at=now,
                )

        for tag_name in actions.get("add_tags", []) or []:
            tag, _ = TicketTag.objects.get_or_create(name=str(tag_name).strip())
            ticket.tags.add(tag)

        if actions.get("notify_requester") and ticket.requester:
            NotificationService.notify_ticket_users(
                ticket,
                actions.get("notification_message") or f"Ticket #{ticket.id} actualizado",
                actor=actor,
            )
