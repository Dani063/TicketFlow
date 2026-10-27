"""Presencia efímera de agentes en BBDD, independiente de workers y réplicas."""
from datetime import timedelta

from django.utils import timezone

from app.api import APIValidationError
from app.models import TicketPresence, User
from app.permissions import agent_user_filter, is_agent


PRESENCE_TTL_SECONDS = 45


def heartbeat(ticket, actor, *, composing=False, leave=False, seen_at=None):
    if not actor.is_authenticated or not actor.is_active or not is_agent(actor):
        raise APIValidationError('forbidden', 'La presencia solo está disponible para agentes.', status=403)
    if type(composing) is not bool:
        raise APIValidationError('invalid_composing', 'composing debe ser un booleano.', status=400)
    now = timezone.now()
    cutoff = now - timedelta(seconds=PRESENCE_TTL_SECONDS)
    TicketPresence.objects.filter(last_seen__lte=cutoff).delete()
    if leave:
        # Un beacon tardío no debe borrar el latido de una pestaña recién abierta.
        if seen_at is not None:
            TicketPresence.objects.filter(ticket=ticket, user=actor, last_seen__lte=seen_at).delete()
        stamp = None
    else:
        TicketPresence.objects.update_or_create(ticket=ticket, user=actor,
                                               defaults={'last_seen': now, 'composing': composing})
        stamp = now.isoformat()
    eligible = User.objects.filter(agent_user_filter(), is_active=True).values('pk')
    peers = TicketPresence.objects.filter(ticket=ticket, last_seen__gt=cutoff,
                                          user_id__in=eligible).exclude(user=actor).select_related('user').order_by('user__name', 'user_id')
    return {'others': [{'id': row.user_id, 'name': row.user.name or row.user.email, 'composing': row.composing}
                       for row in peers], 'last_seen': stamp, 'ttl_seconds': PRESENCE_TTL_SECONDS}
