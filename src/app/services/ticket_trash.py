"""Borrado lógico y restauración, con actor y auditoría por transición real."""
from django.db import transaction
from django.utils import timezone

from app.api import APIValidationError
from app.models import Ticket, TicketEvent
from app.permissions import is_agent


class TicketTrashService:
    @staticmethod
    @transaction.atomic
    def _change(actor, ids, *, deleted):
        if not actor.is_authenticated or not actor.is_active or not is_agent(actor):
            raise APIValidationError('forbidden', 'Solo agentes pueden gestionar la papelera.', status=403)
        if (not isinstance(ids, list) or not ids or
                any(type(pk) is not int or not 0 < pk < 2**63 for pk in ids)):
            raise APIValidationError('invalid_ids', 'ids debe ser una lista de enteros positivos.', status=400)
        tickets = list(Ticket.objects.select_for_update().filter(
            pk__in=ids, merged_into__isnull=True, is_deleted=not deleted,
        ).order_by('pk'))
        if not tickets:
            return 0
        now = timezone.now()
        Ticket.objects.filter(pk__in=[t.pk for t in tickets]).update(
            is_deleted=deleted, deleted_at=now if deleted else None,
            deleted_by=actor if deleted else None, updated_at=now,
        )
        TicketEvent.objects.bulk_create([
            TicketEvent(ticket=t, actor=actor, field_name='deleted' if deleted else 'restored',
                        old_value='active' if deleted else 'deleted',
                        new_value='deleted' if deleted else 'active', created_at=now)
            for t in tickets
        ])
        return len(tickets)

    @classmethod
    def delete(cls, actor, ids):
        return cls._change(actor, ids, deleted=True)

    @classmethod
    def restore(cls, actor, ids):
        return cls._change(actor, ids, deleted=False)
