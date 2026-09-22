"""Construccion y mantenimiento de las tablas de hechos de /reporting/.

Este modulo es el unico sitio que paga el coste de releer auditorias y
comentarios. Lo hace fuera del ciclo de peticion (tarea de beat o backfill
manual) y deja el resultado en ``TicketFact`` y ``TicketStatusInterval``, de
modo que un informe pase a ser un agregado acotado sobre esas dos tablas.

Ambas son derivados puros del modelo transaccional: se pueden vaciar y
regenerar enteras sin perder informacion de negocio.
"""

import logging

from django.db import connection, transaction
from django.db.models import F, Q
from django.utils import timezone

from app.models import (
    Comment, ReportingFactState, Ticket, TicketEvent, TicketFact, TicketStatusInterval,
)
from app.services.reporting_metrics import (
    LIFECYCLE_EVENT_NAMES,
    _lifecycle_facts_from_source,
    _status_timeline,
    _ticket_events,
)

logger = logging.getLogger(__name__)

# Lote de tickets por vuelta. Marca el techo de memoria del refresco: por cada
# ticket del lote se cargan sus eventos y comentarios.
DEFAULT_BATCH_SIZE = 500

# Campos que se reescriben en un upsert. La clave (ticket_id) queda fuera.
_UPDATE_FIELDS = [
    "created_at", "updated_at", "sla_breached_at",
    "agent_replies", "first_reply_at", "first_reply_hours",
    "first_assignment_at", "first_assignment_hours",
    "first_resolution_at", "first_resolution_hours",
    "full_resolution_at", "full_resolution_hours",
    "last_assignment_at", "assignment_to_resolution_hours",
    "assignee_stations", "group_stations", "reopens",
    "requester_wait_seconds", "requester_wait_open_since",
    "source_updated_at", "computed_at",
]


def _tickets_for(ticket_ids):
    """Carga los tickets con lo justo para reconstruir sus hechos."""
    return list(
        Ticket.objects.filter(id__in=ticket_ids).only(
            # Exactamente lo que consumen _lifecycle_facts_from_source y
            # _status_timeline; nada mas, para no arrastrar descripciones.
            "id", "status", "assignee_id", "assigned_group_id",
            "created_at", "updated_at", "closed_at", "resolved_at",
            "first_responded_at", "sla_breached_at",
        )
    )


def _fact_kwargs(ticket, fact, now):
    """Traduce un dict de hechos calculado al alta en ``TicketFact``."""
    return {
        "ticket_id": ticket.id,
        # Marcas de tiempo con las que se acotan las consultas del informe.
        "created_at": ticket.created_at,
        "updated_at": ticket.updated_at,
        "sla_breached_at": ticket.sla_breached_at,
        # Hechos del ciclo de vida.
        "agent_replies": fact["agent_replies"],
        "first_reply_at": fact["first_reply_at"],
        "first_reply_hours": fact["first_reply_hours"],
        "first_assignment_at": fact["first_assignment_at"],
        "first_assignment_hours": fact["first_assignment_hours"],
        "first_resolution_at": fact["first_resolution_at"],
        "first_resolution_hours": fact["first_resolution_hours"],
        "full_resolution_at": fact["full_resolution_at"],
        "full_resolution_hours": fact["full_resolution_hours"],
        "last_assignment_at": fact["last_assignment_at"],
        "assignment_to_resolution_hours": fact["assignment_to_resolution_hours"],
        "assignee_stations": fact["assignee_stations"],
        "group_stations": fact["group_stations"],
        "reopens": fact["reopens"],
        "requester_wait_seconds": fact["requester_wait_seconds"],
        "requester_wait_open_since": fact["requester_wait_open_since"],
        "source_updated_at": ticket.updated_at,
        "computed_at": now,
    }


@transaction.atomic
def refresh_ticket_facts(ticket_ids, now=None):
    """Recalcula los hechos y los tramos de estado de los tickets indicados.

    Es idempotente: reescribe por completo la fila de hechos y los tramos de cada
    ticket del lote, asi que repetir la llamada no duplica nada. Devuelve cuantos
    tickets se han procesado.
    """
    ticket_ids = list(ticket_ids)
    if not ticket_ids:
        return 0
    now = now or timezone.now()

    tickets = _tickets_for(ticket_ids)
    if not tickets:
        # Los tickets han desaparecido entre la seleccion y el refresco: los
        # hechos huerfanos caen solos por el ON DELETE CASCADE.
        return 0

    facts = _lifecycle_facts_from_source(tickets, now=now)
    # El upsert se expresa distinto segun el motor: MySQL lo resuelve con
    # ON DUPLICATE KEY UPDATE y NO admite que se le diga por que columna, asi
    # que pasarle unique_fields levanta NotSupportedError; PostgreSQL y SQLite,
    # al reves, lo exigen. El flag de Django es justo esa diferencia.
    upsert = {'update_conflicts': True, 'update_fields': _UPDATE_FIELDS}
    if connection.features.supports_update_conflicts_with_target:
        upsert['unique_fields'] = ['ticket']
    TicketFact.objects.bulk_create(
        [TicketFact(**_fact_kwargs(t, facts[t.id], now)) for t in tickets],
        **upsert,
    )

    # Los tramos de estado se reconstruyen enteros: son pocos por ticket y
    # llevar un diff incremental costaria mas que rehacerlos.
    present_ids = [t.id for t in tickets]
    state_events = _ticket_events(
        present_ids, field_names=LIFECYCLE_EVENT_NAMES, include_actor=False,
    )
    TicketStatusInterval.objects.filter(ticket_id__in=present_ids).delete()
    intervals = [
        TicketStatusInterval(
            ticket_id=ticket.id, status=status, started_at=started_at, ended_at=ended_at,
        )
        for ticket in tickets
        for status, started_at, ended_at in _status_timeline(ticket, state_events.get(ticket.id, []))
    ]
    TicketStatusInterval.objects.bulk_create(intervals, batch_size=1000)
    return len(tickets)


def stale_ticket_ids():
    """Tickets cuyos hechos faltan o se han quedado atras.

    La deteccion es autorreparable y no guarda estado propio: compara el
    ``updated_at`` del ticket con el que tenia cuando se calcularon sus hechos.
    Si el beat estuvo caido, la siguiente pasada recupera todo el retraso sola.

    Comentar o cambiar de estado un ticket toca su ``updated_at``, asi que esa
    comparacion cubre el caso normal; las otras dos consultas son la red de
    seguridad para auditorias escritas sin pasar por ``Ticket.save()``.

    La comparacion con ``computed_at`` es ``>=`` y no ``>`` a proposito:
    ``computed_at`` se sella *antes* de leer los eventos, asi que uno escrito
    durante el propio refresco comparte marca de tiempo con el. Con ``>`` se
    perderia para siempre; con ``>=`` como mucho se recalcula una vez de mas, y
    el refresco es idempotente. No genera trabajo perpetuo: la siguiente pasada
    sella un ``computed_at`` ya posterior a ese evento.
    """
    changed = Ticket.objects.filter(
        Q(fact__isnull=True) | Q(updated_at__gt=F("fact__source_updated_at"))
    ).values_list("id", flat=True)

    touched = TicketEvent.objects.filter(
        created_at__gte=F("ticket__fact__computed_at")
    ).values_list("ticket_id", flat=True)

    commented = Comment.objects.filter(
        created_at__gte=F("ticket__fact__computed_at")
    ).values_list("ticket_id", flat=True)

    return sorted(set(changed) | set(touched) | set(commented))


def mark_facts_complete(now=None):
    """Deja constancia de que los hechos cubren todo el historico.

    Es lo que autoriza a /reporting/ a leer las tablas derivadas en vez de
    reconstruir desde la auditoria. Se reescribe en cada pasada completa, asi que
    ``backfilled_at`` vale tambien como latido del refresco.
    """
    ReportingFactState.objects.update_or_create(
        singleton=1,
        defaults={"backfilled_at": now or timezone.now(), "tickets": TicketFact.objects.count()},
    )


def refresh_stale_facts(batch_size=DEFAULT_BATCH_SIZE):
    """Pone al dia los hechos rancios. Es lo que invoca la tarea de beat.

    Procesa siempre *todo* lo pendiente antes de marcar la pasada como completa:
    parar a medias y dar los hechos por buenos publicaria una cola incompleta.
    En un despliegue sin backfill previo, esta primera pasada hace el historico
    entero — lenta, pero en el worker y sin tocar el pool web.
    """
    pending = stale_ticket_ids()
    processed = 0
    for start in range(0, len(pending), batch_size):
        processed += refresh_ticket_facts(pending[start:start + batch_size])
    mark_facts_complete()
    if processed:
        logger.info(
            "reporting_facts_refreshed",
            extra={"tickets": processed, "pending": len(pending)},
        )
    return processed


def rebuild_all_facts(batch_size=DEFAULT_BATCH_SIZE, progress=None):
    """Backfill completo. Lo usa `python main.py rebuild_reporting_facts`.

    Recorre los tickets por lotes de ``batch_size`` en vez de cargarlos todos:
    con el historico importado de Zendesk, una sola pasada en memoria es
    justamente lo que este rediseno viene a eliminar.
    """
    ids = list(Ticket.objects.order_by("id").values_list("id", flat=True))
    processed = 0
    for start in range(0, len(ids), batch_size):
        processed += refresh_ticket_facts(ids[start:start + batch_size])
        if progress:
            progress(processed, len(ids))
    mark_facts_complete()
    return processed
