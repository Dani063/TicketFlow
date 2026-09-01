"""Métricas nativas de TicketFlow, organizadas por zonas de negocio."""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from statistics import median

from django.db.models import Q
from django.utils import timezone

from app.models import Comment, ProductLine, SatisfactionRating, Ticket, TicketEvent
from app.permissions import is_agent


UNSOLVED_STATUSES = ("open", "pending")
SOLVED_STATUSES = ("resolved", "closed")
STATUS_EVENT_NAMES = ("status", "estado")
ASSIGNEE_EVENT_NAMES = ("assignee_id", "assignee", "asignado")
GROUP_EVENT_NAMES = ("group_id", "group", "grupo")

STATUS_ALIASES = {
    "new": "open",
    "open": "open",
    "pending": "pending",
    "hold": "pending",
    "on-hold": "pending",
    "on_hold": "pending",
    "solved": "resolved",
    "resolved": "resolved",
    "closed": "closed",
}

DIMENSION_LABELS = {
    "product": "Producto / servicio",
}


def _int(value):
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ReportFilters:
    product_line_id: int = None
    unclassified_product: bool = False
    group_id: int = None
    assignee_id: int = None
    organization_id: int = None
    channel: str = ""
    priority: str = ""
    ticket_type: str = ""

    @classmethod
    def from_params(cls, params):
        product = (params.get("product") or "").strip()
        return cls(
            product_line_id=_int(product),
            unclassified_product=product == "unclassified",
            group_id=_int(params.get("group")),
            assignee_id=_int(params.get("assignee")),
            organization_id=_int(params.get("organization")),
            channel=(params.get("channel") or "").strip(),
            priority=(params.get("priority") or "").strip(),
            ticket_type=(params.get("type") or "").strip(),
        )

    def apply(self, qs, include_assignee=True):
        if self.unclassified_product:
            qs = qs.filter(product_line__isnull=True)
        elif self.product_line_id:
            qs = qs.filter(product_line_id=self.product_line_id)
        if self.group_id:
            qs = qs.filter(assigned_group_id=self.group_id)
        if include_assignee and self.assignee_id:
            qs = qs.filter(assignee_id=self.assignee_id)
        if self.organization_id:
            qs = qs.filter(requester__organization_id=self.organization_id)
        if self.channel:
            qs = qs.filter(channel=self.channel)
        if self.priority:
            qs = qs.filter(priority=self.priority)
        if self.ticket_type:
            qs = qs.filter(type=self.ticket_type)
        return qs

    def cache_fragment(self):
        return ":".join(str(value or "-") for value in (
            self.product_line_id, self.unclassified_product, self.group_id, self.assignee_id,
            self.organization_id, self.channel,
            self.priority, self.ticket_type,
        ))


def live_ticket_queryset(filters):
    qs = Ticket.objects.filter(is_deleted=False, merged_into__isnull=True)
    qs = filters.apply(qs)
    return qs.select_related(
        "product_line", "assigned_group", "assignee", "assignee__role",
        "requester", "requester__organization", "created_by", "created_by__role",
    )


def _local_day(value):
    if value is None:
        return None
    if timezone.is_naive(value):
        value = timezone.make_aware(value, timezone.get_current_timezone())
    return timezone.localtime(value).date()


def _iso_day(value):
    day = _local_day(value)
    return day.isoformat() if day else None


def _median(values, digits=1):
    values = [float(v) for v in values if v is not None]
    if not values:
        return None
    return round(float(median(values)), digits)


def _avg(values, digits=1):
    values = [float(v) for v in values if v is not None]
    if not values:
        return None
    return round(sum(values) / len(values), digits)


def _pct(part, total, digits=1):
    if not total:
        return None
    return round(part / total * 100, digits)


def _status(value):
    return STATUS_ALIASES.get((value or "").strip().lower(), (value or "").strip().lower())


def _event_kind(value):
    value = (value or "").strip().lower()
    if value in STATUS_EVENT_NAMES:
        return "status"
    if value in ASSIGNEE_EVENT_NAMES:
        return "assignee"
    if value in GROUP_EVENT_NAMES:
        return "group"
    return value


def _hours(start, end):
    if not start or not end or end < start:
        return None
    return (end - start).total_seconds() / 3600.0


def _dimension_value(ticket, key):
    if key == "product":
        return ticket.product_line.name if ticket.product_line_id and ticket.product_line else "Sin clasificar"
    if key == "group":
        return ticket.assigned_group.group_name if ticket.assigned_group_id and ticket.assigned_group else "Sin grupo"
    if key == "channel":
        return ticket.channel or "Sin canal"
    if key == "priority":
        return ticket.priority or "Sin prioridad"
    if key == "type":
        return ticket.type or "Sin tipo"
    return "Sin valor"


def _day_rows(counter):
    return [{"day": str(day), "n": n} for day, n in sorted(counter.items())]


def _month_key(value):
    day = _local_day(value)
    return f"{day.year:04d}-{day.month:02d}" if day else None


def _ticket_events(ticket_ids):
    if not ticket_ids:
        return defaultdict(list)
    rows = (
        TicketEvent.objects.filter(ticket_id__in=ticket_ids)
        .select_related("actor", "actor__role")
        .order_by("created_at", "id")
    )
    grouped = defaultdict(list)
    for event in rows:
        grouped[event.ticket_id].append(event)
    return grouped


def _agent_comments(ticket_ids):
    grouped = defaultdict(list)
    if not ticket_ids:
        return grouped
    rows = (
        Comment.objects.filter(ticket_id__in=ticket_ids)
        .select_related("user", "user__role")
        .order_by("created_at", "id")
    )
    for comment in rows:
        if is_agent(comment.user):
            grouped[comment.ticket_id].append(comment)
    return grouped


def _lifecycle_facts(tickets, now=None):
    """Reconstruye los hechos por ticket desde comentarios y eventos.

    Los imports historicos usaban nombres de campo en castellano; _event_kind y
    _status los convierten en lectura sin alterar los registros originales.
    """
    now = now or timezone.now()
    ids = [ticket.id for ticket in tickets]
    events = _ticket_events(ids)
    comments = _agent_comments(ids)
    facts = {}

    for ticket in tickets:
        ticket_events = events.get(ticket.id, [])
        status_events = [e for e in ticket_events if _event_kind(e.field_name) == "status"]
        created_event = next((e for e in ticket_events if _event_kind(e.field_name) == "created"), None)

        replies = [c for c in comments.get(ticket.id, []) if c.is_public]
        first_reply_at = min((c.created_at for c in replies), default=None)
        if ticket.first_responded_at and (not first_reply_at or ticket.first_responded_at < first_reply_at):
            first_reply_at = ticket.first_responded_at

        resolutions = [
            e.created_at for e in status_events if _status(e.new_value) in SOLVED_STATUSES
        ]
        if ticket.resolved_at:
            resolutions.append(ticket.resolved_at)
        if ticket.closed_at:
            resolutions.append(ticket.closed_at)
        resolutions = sorted(set(resolutions))
        first_resolution_at = resolutions[0] if resolutions else None
        full_resolution_at = resolutions[-1] if resolutions else None

        assignee_events = [e for e in ticket_events if _event_kind(e.field_name) == "assignee"]
        group_events = [e for e in ticket_events if _event_kind(e.field_name) == "group"]
        assignees = {str(e.new_value).strip() for e in assignee_events if e.new_value not in (None, "", "None")}
        groups = {str(e.new_value).strip() for e in group_events if e.new_value not in (None, "", "None")}
        if ticket.assignee_id:
            assignees.add(str(ticket.assignee_id))
        if ticket.assigned_group_id:
            groups.add(str(ticket.assigned_group_id))

        first_assignment_at = min((e.created_at for e in assignee_events if e.new_value), default=None)
        if not first_assignment_at and ticket.assignee_id:
            first_assignment_at = ticket.created_at
        assignments_before_resolution = [
            e.created_at for e in assignee_events
            if e.new_value and (not full_resolution_at or e.created_at <= full_resolution_at)
        ]
        last_assignment_at = max(assignments_before_resolution, default=first_assignment_at)

        reopen_count = sum(
            1 for e in status_events
            if _status(e.old_value) in SOLVED_STATUSES and _status(e.new_value) in UNSOLVED_STATUSES
        )

        # Requester wait: tiempo en abierto. TicketFlow normaliza new->open y
        # hold->pending, por lo que no se inventa una categoria historica ausente.
        initial_status = _status(created_event.new_value) if created_event else ""
        if not initial_status and status_events:
            initial_status = _status(status_events[0].old_value)
        initial_status = initial_status or ("open" if ticket.created_at else _status(ticket.status))
        cursor = ticket.created_at
        current_status = initial_status
        requester_wait_seconds = 0.0
        stop_at = full_resolution_at or now
        for event in status_events:
            if not cursor or event.created_at < cursor or event.created_at > stop_at:
                continue
            if current_status == "open":
                requester_wait_seconds += (event.created_at - cursor).total_seconds()
            cursor = event.created_at
            current_status = _status(event.new_value) or current_status
        if cursor and stop_at >= cursor and current_status == "open":
            requester_wait_seconds += (stop_at - cursor).total_seconds()

        facts[ticket.id] = {
            "ticket": ticket,
            "agent_replies": len(replies),
            "first_reply_at": first_reply_at,
            "first_reply_hours": _hours(ticket.created_at, first_reply_at),
            "first_assignment_at": first_assignment_at,
            "first_assignment_hours": _hours(ticket.created_at, first_assignment_at),
            "first_resolution_at": first_resolution_at,
            "first_resolution_hours": _hours(ticket.created_at, first_resolution_at),
            "full_resolution_at": full_resolution_at,
            "full_resolution_hours": _hours(ticket.created_at, full_resolution_at),
            "last_assignment_at": last_assignment_at,
            "assignment_to_resolution_hours": _hours(last_assignment_at, full_resolution_at),
            "requester_wait_hours": round(requester_wait_seconds / 3600.0, 4),
            "assignee_stations": len(assignees),
            "group_stations": len(groups),
            "reopens": reopen_count,
            "events": ticket_events,
            "agent_comments": comments.get(ticket.id, []),
        }
    return facts


def _solved_tickets(qs, start_dt, end_dt):
    return list(
        qs.filter(
            Q(resolved_at__range=(start_dt, end_dt))
            | Q(closed_at__range=(start_dt, end_dt))
            | Q(
                events__field_name__in=STATUS_EVENT_NAMES,
                events__new_value__in=("resolved", "closed", "solved"),
                events__created_at__range=(start_dt, end_dt),
            )
        ).distinct()
    )


def _kpi(key, label, value, unit="", note=""):
    return {"key": key, "label": label, "value": value, "unit": unit, "note": note}


def _base_payload(section, date_from, date_to):
    return {
        "ok": True,
        "section": section,
        "range": {"from": str(date_from), "to": str(date_to)},
        "kpis": [],
        "charts": {},
        "tables": [],
        "products": [
            {
                "id": product.id,
                "code": product.code,
                "name": product.name,
                "color": product.color,
                "icon": product.icon,
            }
            for product in ProductLine.objects.filter(active=True).order_by("sort_order", "name")
        ] + [{
            "id": None,
            "code": "unclassified",
            "name": "Sin clasificar",
            "color": "#b45309",
            "icon": "fa-triangle-exclamation",
        }],
    }


def _dimension_ticket_payload(tickets, total=None):
    total = len(tickets) if total is None else total
    payload = {}
    for key in DIMENSION_LABELS:
        counts = Counter(_dimension_value(ticket, key) for ticket in tickets)
        top = counts.most_common(10)
        payload[key] = {
            "label": DIMENSION_LABELS[key],
            "summary": [
                {"label": label, "n": n, "pct": _pct(n, total) or 0}
                for label, n in top
            ],
        }
    return payload


def build_tickets(filters, date_from, date_to, start_dt, end_dt):
    base = live_ticket_queryset(filters)
    created = list(base.filter(created_at__range=(start_dt, end_dt)))
    created_facts = _lifecycle_facts(created)
    solved = _solved_tickets(base, start_dt, end_dt)
    solved_facts = _lifecycle_facts(solved)

    unsolved_created = sum(1 for ticket in created if ticket.status in UNSOLVED_STATUSES)
    one_touch = sum(1 for fact in solved_facts.values() if fact["agent_replies"] < 2)
    reopened = sum(1 for fact in solved_facts.values() if fact["reopens"] > 0)

    payload = _base_payload("tickets", date_from, date_to)
    payload["kpis"] = [
        _kpi("created", "Tickets creados", len(created)),
        _kpi("unsolved", "Tickets no resueltos", unsolved_created),
        _kpi("solved", "Tickets resueltos", len(solved)),
        _kpi("one_touch", "Resueltos con una respuesta", _pct(one_touch, len(solved)), "%"),
        _kpi("reopened", "Tickets reabiertos", _pct(reopened, len(solved)), "%"),
    ]

    hour_counts = Counter(timezone.localtime(t.created_at).hour for t in created)
    weekday_counts = Counter(timezone.localtime(t.created_at).weekday() for t in created)
    occurrences = Counter()
    cursor = date_from
    while cursor <= date_to:
        occurrences[cursor.weekday()] += 1
        cursor += timedelta(days=1)

    created_by_day = Counter(_local_day(t.created_at) for t in created)
    solved_from_created_by_day = Counter(
        _local_day(t.created_at) for t in created if t.status in SOLVED_STATUSES
    )
    dimensions = _dimension_ticket_payload(created)
    for key, dim in dimensions.items():
        top_labels = {row[0] for row in Counter(_dimension_value(t, key) for t in created).most_common(10)}
        by_day = defaultdict(Counter)
        for ticket in created:
            label = _dimension_value(ticket, key)
            if label in top_labels:
                by_day[_local_day(ticket.created_at)][label] += 1
        dim["by_day"] = [
            {"day": str(day), "values": dict(values)} for day, values in sorted(by_day.items())
        ]

    # Comparativa mensual de los ultimos cinco anos; el mes actual no entra hasta
    # que termina, igual que en el informe de referencia.
    anchor = date_to
    first_year = anchor.year - 4
    current_month_start = date(anchor.year, anchor.month, 1)
    month_rows = defaultdict(Counter)
    history_qs = filters.apply(
        Ticket.objects.filter(
            is_deleted=False, merged_into__isnull=True,
            created_at__date__gte=date(first_year, 1, 1),
            created_at__date__lt=current_month_start,
        )
    )
    for created_at in history_qs.values_list("created_at", flat=True):
        local = timezone.localtime(created_at)
        month_rows[local.year][local.month] += 1

    payload["charts"] = {
        "created_by_hour": {
            "labels": [str(h) for h in range(24)],
            "values": [round(hour_counts[h] / len(created) * 100, 1) if created else 0 for h in range(24)],
            "unit": "%",
        },
        "average_by_weekday": {
            "labels": ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"],
            "values": [round(weekday_counts[d] / occurrences[d], 1) if occurrences[d] else 0 for d in range(7)],
        },
        "created_by_date": {
            "created": _day_rows(created_by_day),
            "solved": _day_rows(solved_from_created_by_day),
        },
        "dimensions": dimensions,
        "created_by_month_year": {
            "years": [
                {"year": year, "values": [month_rows[year].get(month, 0) for month in range(1, 13)]}
                for year in range(first_year, anchor.year + 1)
            ],
        },
    }
    return payload


def _brackets(values, rules):
    counts = Counter()
    for value in values:
        for label, predicate in rules:
            if predicate(value):
                counts[label] += 1
                break
    total = sum(counts.values())
    return [{"label": label, "n": counts[label], "pct": _pct(counts[label], total) or 0} for label, _ in rules]


def _fact_daily(facts, date_field, value_fields):
    buckets = defaultdict(lambda: defaultdict(list))
    for fact in facts.values():
        day = _local_day(fact.get(date_field))
        if not day:
            continue
        for field in value_fields:
            value = fact.get(field)
            if value is not None:
                buckets[day][field].append(value)
    return [
        {"day": str(day), **{field: _median(values.get(field, [])) for field in value_fields}}
        for day, values in sorted(buckets.items())
    ]


def build_efficiency(filters, date_from, date_to, start_dt, end_dt):
    base = live_ticket_queryset(filters)
    solved = _solved_tickets(base, start_dt, end_dt)
    facts = _lifecycle_facts(solved)
    created = list(base.filter(created_at__range=(start_dt, end_dt)))
    created_facts = _lifecycle_facts(created)

    fact_values = list(facts.values())
    payload = _base_payload("efficiency", date_from, date_to)
    payload["kpis"] = [
        _kpi("first_reply_median", "Mediana primera respuesta", _median(
            [f["first_reply_hours"] for f in created_facts.values()]
        ), "h"),
        _kpi("full_resolution_median", "Mediana resolucion completa", _median(
            [f["full_resolution_hours"] for f in fact_values]
        ), "h"),
        _kpi("requester_wait_median", "Mediana espera solicitante", _median(
            [f["requester_wait_hours"] for f in fact_values]
        ), "h"),
        _kpi("assignee_stations_average", "Promedio de asignados por ticket", _avg(
            [f["assignee_stations"] for f in fact_values]
        )),
        _kpi("group_stations_average", "Promedio de grupos por ticket", _avg(
            [f["group_stations"] for f in fact_values]
        )),
    ]

    group_brackets = _brackets(
        [f["group_stations"] for f in fact_values],
        [("1", lambda v: v <= 1), ("2", lambda v: v == 2), ("3", lambda v: v == 3), (">3", lambda v: v > 3)],
    )
    reply_brackets = _brackets(
        [f["agent_replies"] for f in fact_values],
        [("0", lambda v: v == 0), ("1", lambda v: v == 1), ("2", lambda v: v == 2),
         ("3-5", lambda v: 3 <= v <= 5), (">5", lambda v: v > 5)],
    )

    station_daily = defaultdict(lambda: {"assignee": [], "group": []})
    replies_daily = defaultdict(lambda: {"replies": [], "solved": 0})
    resolution_daily = defaultdict(lambda: {"full": [], "wait": []})
    for fact in fact_values:
        day = _local_day(fact["full_resolution_at"])
        if not day:
            continue
        station_daily[day]["assignee"].append(fact["assignee_stations"])
        station_daily[day]["group"].append(fact["group_stations"])
        replies_daily[day]["replies"].append(fact["agent_replies"])
        replies_daily[day]["solved"] += 1
        if fact["full_resolution_hours"] is not None:
            resolution_daily[day]["full"].append(fact["full_resolution_hours"])
        if fact["requester_wait_hours"] is not None:
            resolution_daily[day]["wait"].append(fact["requester_wait_hours"])

    payload["charts"] = {
        "group_stations_brackets": group_brackets,
        "agent_replies_brackets": reply_brackets,
        "first_reply_assignment_by_date": [
            {
                "day": str(day),
                "first_reply": _median([f["first_reply_hours"] for f in created_facts.values() if _local_day(f["ticket"].created_at) == day]),
                "first_assignment": _median([f["first_assignment_hours"] for f in created_facts.values() if _local_day(f["ticket"].created_at) == day]),
            }
            for day in sorted({_local_day(f["ticket"].created_at) for f in created_facts.values() if f["ticket"].created_at})
        ],
        "resolution_wait_by_date": [
            {"day": str(day), "full_resolution": _median(values["full"]), "requester_wait": _median(values["wait"])}
            for day, values in sorted(resolution_daily.items())
        ],
        "stations_by_date": [
            {"day": str(day), "assignee": _avg(values["assignee"]), "group": _avg(values["group"])}
            for day, values in sorted(station_daily.items())
        ],
        "replies_resolutions_by_date": [
            {"day": str(day), "agent_replies": _avg(values["replies"]), "solved": values["solved"]}
            for day, values in sorted(replies_daily.items())
        ],
    }
    return payload


def build_assignee_activity(filters, date_from, date_to, start_dt, end_dt):
    base = live_ticket_queryset(filters)
    solved = _solved_tickets(base, start_dt, end_dt)
    facts = _lifecycle_facts(solved)
    fact_values = list(facts.values())
    solved_ids = [ticket.id for ticket in solved]

    ratings = list(
        SatisfactionRating.objects.filter(ticket_id__in=solved_ids, score__in=("good", "bad"))
        .select_related("ticket", "assignee")
    ) if solved_ids else []
    good = sum(1 for rating in ratings if rating.score == "good")
    bad = sum(1 for rating in ratings if rating.score == "bad")
    one_touch = sum(1 for fact in fact_values if fact["agent_replies"] < 2)
    two_touch = sum(1 for fact in fact_values if fact["agent_replies"] == 2)

    payload = _base_payload("assignee_activity", date_from, date_to)
    payload["kpis"] = [
        _kpi("solved", "Tickets resueltos", len(solved)),
        _kpi("assigned_unsolved", "No resueltos asignados", base.filter(
            status__in=UNSOLVED_STATUSES, assignee__isnull=False
        ).count()),
        _kpi("assignment_to_resolution", "Asignacion a resolucion", _median(
            [fact["assignment_to_resolution_hours"] for fact in fact_values]
        ), "h"),
        _kpi("one_touch", "Resueltos con una respuesta", _pct(one_touch, len(solved)), "%"),
        _kpi("two_touch", "Resueltos con dos respuestas", _pct(two_touch, len(solved)), "%"),
    ]

    resolution_brackets = _brackets(
        [fact["full_resolution_hours"] for fact in fact_values if fact["full_resolution_hours"] is not None],
        [
            ("0-5 h", lambda v: v <= 5),
            ("5-24 h", lambda v: 5 < v <= 24),
            ("1-7 dias", lambda v: 24 < v <= 168),
            ("7-30 dias", lambda v: 168 < v <= 720),
            (">30 dias", lambda v: v > 720),
        ],
    )

    ratings_by_ticket = defaultdict(list)
    for rating in ratings:
        ratings_by_ticket[rating.ticket_id].append(rating)
    score_wait_daily = defaultdict(lambda: {"good": 0, "bad": 0, "wait": []})
    for fact in fact_values:
        day = _local_day(fact["full_resolution_at"])
        if not day:
            continue
        if fact["requester_wait_hours"] is not None:
            score_wait_daily[day]["wait"].append(fact["requester_wait_hours"])
        for rating in ratings_by_ticket.get(fact["ticket"].id, []):
            score_wait_daily[day][rating.score] += 1

    agent_facts = defaultdict(list)
    for fact in fact_values:
        agent = fact["ticket"].assignee.name if fact["ticket"].assignee_id and fact["ticket"].assignee else "Sin asignar"
        agent_facts[agent].append(fact)
    agent_ratings = defaultdict(lambda: {"good": 0, "bad": 0})
    for rating in ratings:
        agent = rating.assignee.name if rating.assignee_id and rating.assignee else "Sin asignar"
        agent_ratings[agent][rating.score] += 1

    rows = []
    for agent, values in agent_facts.items():
        sat = agent_ratings[agent]
        rows.append({
            "agent": agent,
            "solved": len(values),
            "first_reply_hours": _median([f["first_reply_hours"] for f in values]),
            "requester_wait_hours": _median([f["requester_wait_hours"] for f in values]),
            "assignment_to_resolution_hours": _median([f["assignment_to_resolution_hours"] for f in values]),
            "full_resolution_hours": _median([f["full_resolution_hours"] for f in values]),
            "satisfaction_pct": _pct(sat["good"], sat["good"] + sat["bad"]),
            "one_touch_pct": _pct(sum(1 for f in values if f["agent_replies"] < 2), len(values)),
        })
    rows.sort(key=lambda row: (-row["solved"], row["agent"]))

    payload["charts"] = {
        "satisfaction": [
            {"label": "Buenas", "n": good, "pct": _pct(good, good + bad) or 0},
            {"label": "Malas", "n": bad, "pct": _pct(bad, good + bad) or 0},
        ],
        "resolution_brackets": resolution_brackets,
        "satisfaction_wait_by_date": [
            {
                "day": str(day),
                "satisfaction_pct": _pct(values["good"], values["good"] + values["bad"]),
                "requester_wait_hours": _median(values["wait"]),
            }
            for day, values in sorted(score_wait_daily.items())
        ],
    }
    payload["tables"] = [{
        "key": "assignee_activity",
        "columns": [
            "Agente", "Resueltos", "Primera respuesta (h)", "Espera solicitante (h)",
            "Asignación a resolución (h)", "Resolución completa (h)", "CSAT", "Una respuesta",
        ],
        "rows": rows[:50],
    }]
    return payload


def build_agent_updates(filters, date_from, date_to, start_dt, end_dt):
    base = live_ticket_queryset(filters)
    ticket_ids = base.values_list("id", flat=True)

    comments = list(
        Comment.objects.filter(ticket_id__in=ticket_ids, created_at__range=(start_dt, end_dt))
        .select_related("user", "user__role", "ticket")
        .order_by("created_at", "id")
    )
    comments = [comment for comment in comments if is_agent(comment.user)]
    events = list(
        TicketEvent.objects.filter(ticket_id__in=ticket_ids, created_at__range=(start_dt, end_dt))
        .select_related("actor", "actor__role", "ticket")
        .order_by("created_at", "id")
    )
    agent_events = [event for event in events if event.actor_id and is_agent(event.actor)]
    solved_events = [
        event for event in agent_events
        if _event_kind(event.field_name) == "status" and _status(event.new_value) in SOLVED_STATUSES
    ]
    created_tickets = [
        ticket for ticket in base.filter(created_at__range=(start_dt, end_dt))
        if ticket.created_by_id and is_agent(ticket.created_by)
    ]

    public_comments = [comment for comment in comments if comment.is_public]
    internal_comments = [comment for comment in comments if not comment.is_public]
    commented_ticket_ids = {comment.ticket_id for comment in comments}

    payload = _base_payload("agent_updates", date_from, date_to)
    payload["kpis"] = [
        _kpi("public_comments", "Comentarios publicos", len(public_comments)),
        _kpi("internal_comments", "Comentarios internos", len(internal_comments)),
        _kpi("tickets_commented", "Tickets comentados", len(commented_ticket_ids)),
        _kpi("tickets_solved", "Tickets resueltos", len({event.ticket_id for event in solved_events})),
        _kpi("tickets_created", "Tickets creados", len(created_tickets)),
    ]

    comment_daily = defaultdict(lambda: {"comments": 0, "public": 0, "internal": 0, "tickets": set()})
    for comment in comments:
        day = _local_day(comment.created_at)
        comment_daily[day]["comments"] += 1
        comment_daily[day]["public" if comment.is_public else "internal"] += 1
        comment_daily[day]["tickets"].add(comment.ticket_id)

    activity_daily = defaultdict(lambda: {"commented": set(), "solved": set(), "created": set()})
    for comment in comments:
        activity_daily[_local_day(comment.created_at)]["commented"].add(comment.ticket_id)
    for event in solved_events:
        activity_daily[_local_day(event.created_at)]["solved"].add(event.ticket_id)
    for ticket in created_tickets:
        activity_daily[_local_day(ticket.created_at)]["created"].add(ticket.id)

    by_agent = defaultdict(lambda: {
        "events": 0, "comments": 0, "public": 0, "internal": 0,
        "commented": set(), "solved": set(), "created": set(),
    })
    for event in agent_events:
        agent = event.actor.name or event.actor.email
        by_agent[agent]["events"] += 1
        if event in solved_events:
            by_agent[agent]["solved"].add(event.ticket_id)
    for comment in comments:
        agent = comment.user.name or comment.user.email
        by_agent[agent]["comments"] += 1
        by_agent[agent]["public" if comment.is_public else "internal"] += 1
        by_agent[agent]["commented"].add(comment.ticket_id)
    for ticket in created_tickets:
        agent = ticket.created_by.name or ticket.created_by.email
        by_agent[agent]["created"].add(ticket.id)

    table_rows = []
    for agent, values in by_agent.items():
        table_rows.append({
            "agent": agent,
            "updates": values["events"] + values["comments"],
            "comments": values["comments"],
            "public_comments": values["public"],
            "internal_comments": values["internal"],
            "tickets_commented": len(values["commented"]),
            "tickets_solved": len(values["solved"]),
            "tickets_created": len(values["created"]),
        })
    table_rows.sort(key=lambda row: (-row["updates"], row["agent"]))

    payload["charts"] = {
        "comment_averages_by_date": [
            {
                "day": str(day),
                "comments": values["comments"],
                "public_per_ticket": round(values["public"] / len(values["tickets"]), 2) if values["tickets"] else 0,
                "internal_per_ticket": round(values["internal"] / len(values["tickets"]), 2) if values["tickets"] else 0,
            }
            for day, values in sorted(comment_daily.items())
        ],
        "activity_by_date": [
            {
                "day": str(day),
                "commented": len(values["commented"]),
                "solved": len(values["solved"]),
                "created": len(values["created"]),
            }
            for day, values in sorted(activity_daily.items())
        ],
    }
    payload["tables"] = [{
        "key": "agent_updates",
        "columns": [
            "Agente", "Actualizaciones", "Comentarios", "Publicos", "Internos",
            "Tickets comentados", "Tickets resueltos", "Tickets creados",
        ],
        "rows": table_rows[:50],
    }]
    return payload


def build_unsolved(filters, date_from, date_to, start_dt, end_dt):
    candidates = list(live_ticket_queryset(filters).filter(created_at__lte=end_dt))
    events = _ticket_events([ticket.id for ticket in candidates])
    states = {
        ticket.id: _state_at(ticket, events.get(ticket.id, []), end_dt)
        for ticket in candidates
    }
    tickets = [ticket for ticket in candidates if states[ticket.id] in UNSOLVED_STATUSES]
    facts = _lifecycle_facts(tickets)
    now = end_dt
    age_hours = [_hours(ticket.created_at, now) for ticket in tickets]
    update_hours = [_hours(ticket.updated_at, now) for ticket in tickets]
    unreplied = sum(1 for fact in facts.values() if fact["agent_replies"] < 1)

    payload = _base_payload("unsolved", date_from, date_to)
    payload["kpis"] = [
        _kpi("unsolved", "Tickets no resueltos", len(tickets)),
        _kpi("new_open", "Tickets abiertos", sum(1 for ticket in tickets if states[ticket.id] == "open")),
        _kpi("unreplied", "No resueltos sin respuesta", unreplied),
        _kpi("since_update", "Mediana desde actualizacion", _median(update_hours), "h"),
        _kpi("age", "Mediana de antiguedad", _median(age_hours), "h"),
    ]

    status_counts = Counter(states[ticket.id] for ticket in tickets)
    open_tickets = [ticket for ticket in tickets if states[ticket.id] == "open"]
    assignment_counts = Counter("Asignados" if ticket.assignee_id else "Sin asignar" for ticket in open_tickets)

    dimensions = {}
    for key in DIMENSION_LABELS:
        totals = Counter(_dimension_value(ticket, key) for ticket in tickets)
        top_labels = [label for label, _ in totals.most_common(10)]
        rows = []
        for label in top_labels:
            matching = [ticket for ticket in tickets if _dimension_value(ticket, key) == label]
            rows.append({
                "label": label,
                "open": sum(1 for ticket in matching if states[ticket.id] == "open"),
                "pending": sum(1 for ticket in matching if states[ticket.id] == "pending"),
            })
        dimensions[key] = {"label": DIMENSION_LABELS[key], "rows": rows}

    creation_month = defaultdict(Counter)
    for ticket in tickets:
        creation_month[_month_key(ticket.created_at)][states[ticket.id]] += 1

    by_agent = defaultdict(list)
    for ticket in tickets:
        agent = ticket.assignee.name if ticket.assignee_id and ticket.assignee else "Sin asignar"
        by_agent[agent].append(ticket)
    table_rows = []
    for agent, values in by_agent.items():
        table_rows.append({
            "agent": agent,
            "unsolved": len(values),
            "since_update_hours": _median([_hours(ticket.updated_at, now) for ticket in values]),
            "age_hours": _median([_hours(ticket.created_at, now) for ticket in values]),
        })
    table_rows.sort(key=lambda row: (-row["unsolved"], row["agent"]))

    payload["charts"] = {
        "by_status": [{"label": status, "n": n, "pct": _pct(n, len(tickets)) or 0} for status, n in status_counts.items()],
        "assignment_status": [
            {"label": label, "n": n, "pct": _pct(n, len(open_tickets)) or 0}
            for label, n in assignment_counts.items()
        ],
        "dimensions": dimensions,
        "by_creation_month": [
            {"month": month, "open": values["open"], "pending": values["pending"]}
            for month, values in sorted(creation_month.items())
        ],
    }
    payload["tables"] = [{
        "key": "unsolved",
        "columns": ["Agente", "No resueltos", "Desde actualizacion (h)", "Antiguedad (h)"],
        "rows": table_rows[:50],
    }]
    return payload


def _state_at(ticket, events, sample_at):
    if not ticket.created_at or ticket.created_at > sample_at:
        return None
    relevant = [event for event in events if event.created_at <= sample_at]
    created_event = next((event for event in relevant if _event_kind(event.field_name) == "created"), None)
    status_events = [event for event in relevant if _event_kind(event.field_name) == "status"]
    state = _status(created_event.new_value) if created_event else ""
    if not state and status_events:
        state = _status(status_events[0].old_value)
    state = state or "open"
    for event in status_events:
        state = _status(event.new_value) or state

    # Los imports antiguos pueden no traer audits. closed_at/resolved_at permite
    # reconstruir al menos el periodo previo a la resolucion.
    if not status_events:
        solved_at = ticket.resolved_at or ticket.closed_at
        if solved_at and sample_at >= solved_at:
            state = ticket.status if ticket.status in SOLVED_STATUSES else "resolved"
        elif solved_at and sample_at < solved_at:
            state = "open"
        elif sample_at.date() >= timezone.localdate():
            state = ticket.status
    return state


def _sample_at(day):
    return timezone.make_aware(datetime.combine(day, time.max), timezone.get_current_timezone())


def build_backlog(filters, date_from, date_to, start_dt, end_dt):
    anchor = date_to
    daily_days = [anchor - timedelta(days=offset) for offset in reversed(range(30))]
    weekly_days = [anchor - timedelta(days=7 * offset) for offset in reversed(range(12))]
    oldest = min(daily_days + weekly_days)

    tickets = list(
        live_ticket_queryset(filters).filter(created_at__date__lte=anchor)
    )
    events = _ticket_events([ticket.id for ticket in tickets])

    def snapshot(day):
        at = _sample_at(day)
        rows = []
        for ticket in tickets:
            state = _state_at(ticket, events.get(ticket.id, []), at)
            if state in UNSOLVED_STATUSES:
                rows.append((ticket, state))
        return rows

    daily_snapshots = {day: snapshot(day) for day in daily_days}
    weekly_snapshots = {day: snapshot(day) for day in weekly_days}

    dimensions = {}
    for key in DIMENSION_LABELS:
        latest_counts = Counter(_dimension_value(ticket, key) for ticket, _ in weekly_snapshots[weekly_days[-1]])
        top_labels = [label for label, _ in latest_counts.most_common(10)]
        series = []
        for day, rows in weekly_snapshots.items():
            counts = Counter(_dimension_value(ticket, key) for ticket, _ in rows)
            series.append({"day": str(day), "values": {label: counts[label] for label in top_labels}})
        dimensions[key] = {"label": DIMENSION_LABELS[key], "series": series, "labels": top_labels}

    payload = _base_payload("backlog", date_from, date_to)
    payload["charts"] = {
        "daily_by_status": [
            {
                "day": str(day),
                "open": sum(1 for _, status in rows if status == "open"),
                "pending": sum(1 for _, status in rows if status == "pending"),
            }
            for day, rows in daily_snapshots.items()
        ],
        "weekly_by_status": [
            {
                "day": str(day),
                "open": sum(1 for _, status in rows if status == "open"),
                "pending": sum(1 for _, status in rows if status == "pending"),
            }
            for day, rows in weekly_snapshots.items()
        ],
        "dimensions": dimensions,
        "coverage": {"from": str(oldest), "to": str(anchor)},
    }
    return payload


def _rating_dimensions(ratings, tickets_by_id):
    payload = {}
    for key in DIMENSION_LABELS:
        grouped = defaultdict(lambda: {"good": 0, "bad": 0})
        for rating in ratings:
            ticket = tickets_by_id.get(rating.ticket_id)
            if not ticket:
                continue
            grouped[_dimension_value(ticket, key)][rating.score] += 1
        ordered = sorted(grouped.items(), key=lambda item: -(item[1]["good"] + item[1]["bad"]))[:10]
        payload[key] = {
            "label": DIMENSION_LABELS[key],
            "rows": [
                {
                    "label": label,
                    "good": values["good"],
                    "bad": values["bad"],
                    "pct": _pct(values["good"], values["good"] + values["bad"]),
                    "total": values["good"] + values["bad"],
                }
                for label, values in ordered
            ],
        }
    return payload


def build_satisfaction(filters, date_from, date_to, start_dt, end_dt):
    base = live_ticket_queryset(filters)
    solved = _solved_tickets(base, start_dt, end_dt)
    solved_ids = [ticket.id for ticket in solved]
    tickets_by_id = {ticket.id: ticket for ticket in solved}
    all_ratings = list(
        SatisfactionRating.objects.filter(ticket_id__in=solved_ids)
        .select_related("ticket", "assignee")
        .order_by("created_at", "id")
    ) if solved_ids else []
    ratings = [rating for rating in all_ratings if rating.score in ("good", "bad")]
    surveyed = [rating for rating in all_ratings if rating.score in ("offered", "good", "bad")]
    good = sum(1 for rating in ratings if rating.score == "good")
    bad = sum(1 for rating in ratings if rating.score == "bad")

    bad_to_good = TicketEvent.objects.filter(
        ticket_id__in=solved_ids,
        field_name="satisfaction_score",
        old_value="bad",
        new_value="good",
        created_at__range=(start_dt, end_dt),
    ).values("ticket_id").distinct().count() if solved_ids else 0

    payload = _base_payload("satisfaction", date_from, date_to)
    payload["kpis"] = [
        _kpi("score", "Satisfaccion", _pct(good, good + bad), "%"),
        _kpi("good", "Valoraciones buenas", good),
        _kpi("bad", "Valoraciones malas", bad),
        _kpi("bad_to_good", "Cambios de mala a buena", bad_to_good),
        _kpi("rated_ratio", "Tasa de valoracion", _pct(len(ratings), len(surveyed)), "%"),
    ]

    score_comment = Counter()
    for rating in ratings:
        key = f"{rating.score}_{'comment' if (rating.comment or '').strip() else 'no_comment'}"
        score_comment[key] += 1

    daily = defaultdict(lambda: {"good": 0, "bad": 0})
    for rating in ratings:
        daily[_local_day(rating.created_at)][rating.score] += 1

    anchor = date_to
    month_start = date(anchor.year, anchor.month, 1) - timedelta(days=366)
    history_start = timezone.make_aware(
        datetime.combine(month_start, time.min), timezone.get_current_timezone()
    )
    history_base = live_ticket_queryset(filters).filter(
        Q(resolved_at__date__range=(month_start, anchor))
        | Q(closed_at__date__range=(month_start, anchor))
    )
    history_tickets = list(history_base)
    history_by_id = {ticket.id: ticket for ticket in history_tickets}
    history_ratings = list(
        SatisfactionRating.objects.filter(
            ticket_id__in=history_by_id.keys(), created_at__gte=history_start, created_at__lte=end_dt
        )
        .order_by("created_at", "id")
    ) if history_by_id else []
    monthly = defaultdict(lambda: {"good": 0, "bad": 0, "surveyed": 0})
    for rating in history_ratings:
        month = _month_key(rating.created_at)
        if rating.score in ("offered", "good", "bad"):
            monthly[month]["surveyed"] += 1
        if rating.score in ("good", "bad"):
            monthly[month][rating.score] += 1

    payload["charts"] = {
        "good_bad_comments": [
            {"label": "Buenas con comentario", "n": score_comment["good_comment"]},
            {"label": "Buenas sin comentario", "n": score_comment["good_no_comment"]},
            {"label": "Malas con comentario", "n": score_comment["bad_comment"]},
            {"label": "Malas sin comentario", "n": score_comment["bad_no_comment"]},
        ],
        "funnel": [
            {"label": "Todos los tickets", "n": len(solved), "pct": 100 if solved else 0},
            {"label": "Tickets encuestados", "n": len(surveyed), "pct": _pct(len(surveyed), len(solved)) or 0},
            {"label": "Tickets valorados", "n": len(ratings), "pct": _pct(len(ratings), len(solved)) or 0},
        ],
        "score_rated_by_date": [
            {
                "day": str(day),
                "score": _pct(values["good"], values["good"] + values["bad"]),
                "rated": values["good"] + values["bad"],
            }
            for day, values in sorted(daily.items())
        ],
        "dimensions": _rating_dimensions(ratings, tickets_by_id),
        "score_rated_by_month": [
            {
                "month": month,
                "score": _pct(values["good"], values["good"] + values["bad"]),
                "rated": values["good"] + values["bad"],
            }
            for month, values in sorted(monthly.items())
        ][-12:],
        "rated_surveyed_by_month": [
            {
                "month": month,
                "rated_ratio": _pct(values["good"] + values["bad"], values["surveyed"]),
                "surveyed": values["surveyed"],
            }
            for month, values in sorted(monthly.items())
        ][-12:],
    }
    return payload


def _sla_instances(tickets, now=None):
    """Crea instancias reportables con los dos relojes SLA nativos actuales."""
    now = now or timezone.now()
    facts = _lifecycle_facts(tickets, now=now)
    rows = []
    for ticket in tickets:
        fact = facts[ticket.id]
        completed_at = fact["full_resolution_at"]
        if ticket.first_response_met is not None:
            rows.append({
                "ticket": ticket,
                "metric": "Primera respuesta",
                "status": "completed",
                "result": "achieved" if ticket.first_response_met else "breached",
                "updated_at": fact["first_reply_at"] or ticket.sla_breached_at or ticket.updated_at,
                "breached_at": ticket.sla_breached_at if ticket.first_response_met is False else None,
            })
        elif ticket.first_response_due_at:
            breached = ticket.first_response_due_at < now
            rows.append({
                "ticket": ticket,
                "metric": "Primera respuesta",
                "status": "active",
                "result": "breached" if breached else None,
                "updated_at": ticket.sla_breached_at or ticket.updated_at,
                "breached_at": ticket.sla_breached_at if breached else None,
            })

        if ticket.resolution_due_at:
            if completed_at:
                breached = completed_at > ticket.resolution_due_at
                rows.append({
                    "ticket": ticket,
                    "metric": "Resolucion total",
                    "status": "completed",
                    "result": "breached" if breached else "achieved",
                    "updated_at": completed_at,
                    "breached_at": ticket.sla_breached_at if breached else None,
                })
            else:
                breached = ticket.resolution_due_at < now
                rows.append({
                    "ticket": ticket,
                    "metric": "Resolucion total",
                    "status": "active",
                    "result": "breached" if breached else None,
                    "updated_at": ticket.sla_breached_at or ticket.updated_at,
                    "breached_at": ticket.sla_breached_at if breached else None,
                })

        if ticket.sla_breached_at and not any(row["ticket"].id == ticket.id for row in rows):
            rows.append({
                "ticket": ticket,
                "metric": "SLA general",
                "status": "completed" if ticket.status in SOLVED_STATUSES else "active",
                "result": "breached",
                "updated_at": ticket.sla_breached_at,
                "breached_at": ticket.sla_breached_at,
            })
    return rows


def build_sla(filters, date_from, date_to, start_dt, end_dt):
    tickets = list(live_ticket_queryset(filters).filter(
        Q(first_response_met__isnull=False)
        | Q(first_response_due_at__isnull=False)
        | Q(resolution_due_at__isnull=False)
        | Q(sla_breached_at__isnull=False)
    ))
    all_instances = _sla_instances(tickets, now=end_dt)
    instances = [
        row for row in all_instances
        if row["updated_at"] and start_dt <= row["updated_at"] <= end_dt
    ]
    completed = [row for row in instances if row["status"] == "completed"]
    active = [row for row in instances if row["status"] == "active"]
    achieved_tickets = {row["ticket"].id for row in completed if row["result"] == "achieved"}
    breached_tickets = {row["ticket"].id for row in instances if row["result"] == "breached"}
    completed_ticket_ids = {row["ticket"].id for row in completed}
    clean_achieved = achieved_tickets - breached_tickets

    payload = _base_payload("sla", date_from, date_to)
    payload["kpis"] = [
        _kpi("achievement_rate", "Cumplimiento SLA", _pct(len(clean_achieved), len(completed_ticket_ids)), "%"),
        _kpi("breached_tickets", "Tickets SLA incumplidos", len(breached_tickets)),
        _kpi("achieved_tickets", "Tickets SLA cumplidos", len(clean_achieved)),
        _kpi("active_tickets", "Tickets SLA activos", len({row["ticket"].id for row in active})),
        _kpi("active_breached", "Activos incumplidos", len({row["ticket"].id for row in active if row["result"] == "breached"})),
    ]

    completed_daily = defaultdict(lambda: {"achieved": 0, "breached": 0})
    for row in completed:
        completed_daily[_local_day(row["updated_at"])][row["result"]] += 1

    dimensions = {}
    for key in DIMENSION_LABELS:
        grouped = defaultdict(lambda: {"achieved": 0, "breached": 0})
        for row in completed:
            grouped[_dimension_value(row["ticket"], key)][row["result"]] += 1
        ordered = sorted(grouped.items(), key=lambda item: -item[1]["breached"])[:10]
        dimensions[key] = {
            "label": DIMENSION_LABELS[key],
            "rows": [{"label": label, **values} for label, values in ordered],
        }

    breach_hours = Counter()
    breach_weekdays = Counter()
    for row in instances:
        if row["result"] != "breached" or not row["breached_at"]:
            continue
        local = timezone.localtime(row["breached_at"])
        breach_hours[local.hour] += 1
        breach_weekdays[local.weekday()] += 1
    breach_total = sum(breach_hours.values())

    by_metric = defaultdict(lambda: {"achieved": 0, "breached": 0})
    for row in completed:
        by_metric[row["metric"]][row["result"]] += 1

    monthly = defaultdict(lambda: defaultdict(lambda: {"achieved": 0, "breached": 0}))
    for row in all_instances:
        if row["status"] != "completed" or not row["updated_at"]:
            continue
        if row["updated_at"] > end_dt:
            continue
        month = _month_key(row["updated_at"])
        monthly[month][row["metric"]][row["result"]] += 1

    payload["charts"] = {
        "completed_by_date": [
            {"day": str(day), **values} for day, values in sorted(completed_daily.items())
        ],
        "dimensions": dimensions,
        "breaches_by_hour": {
            "labels": [str(hour) for hour in range(24)],
            "values": [round(breach_hours[hour] / breach_total * 100, 1) if breach_total else 0 for hour in range(24)],
        },
        "breaches_by_weekday": {
            "labels": ["Lun", "Mar", "Mie", "Jue", "Vie", "Sab", "Dom"],
            "values": [round(breach_weekdays[day] / breach_total * 100, 1) if breach_total else 0 for day in range(7)],
        },
        "by_metric": [
            {"metric": metric, **values} for metric, values in sorted(by_metric.items())
        ],
        "achievement_by_month": [
            {
                "month": month,
                "metrics": {
                    metric: _pct(values["achieved"], values["achieved"] + values["breached"])
                    for metric, values in metrics.items()
                },
            }
            for month, metrics in sorted(monthly.items())
        ][-12:],
    }
    return payload


def _product_code(ticket):
    return ticket.product_line.code if ticket.product_line_id and ticket.product_line else "unclassified"


def build_executive(filters, date_from, date_to, start_dt, end_dt):
    """Portada ejecutiva: volumen, cola y calidad por producto."""
    base = live_ticket_queryset(filters)
    created = list(base.filter(created_at__range=(start_dt, end_dt)))
    solved = _solved_tickets(base, start_dt, end_dt)

    candidates = list(base.filter(created_at__lte=end_dt))
    events = _ticket_events([ticket.id for ticket in candidates])
    backlog = [
        ticket for ticket in candidates
        if _state_at(ticket, events.get(ticket.id, []), end_dt) in UNSOLVED_STATUSES
    ]

    solved_ids = [ticket.id for ticket in solved]
    ratings = list(SatisfactionRating.objects.filter(
        ticket_id__in=solved_ids, score__in=("good", "bad")
    ).select_related("ticket", "ticket__product_line")) if solved_ids else []

    sla_tickets = list(base.filter(
        Q(first_response_met__isnull=False)
        | Q(first_response_due_at__isnull=False)
        | Q(resolution_due_at__isnull=False)
        | Q(sla_breached_at__isnull=False)
    ))
    sla_instances = [
        row for row in _sla_instances(sla_tickets, now=end_dt)
        if row["updated_at"] and start_dt <= row["updated_at"] <= end_dt
    ]
    completed = [row for row in sla_instances if row["status"] == "completed"]

    created_counts = Counter(_product_code(ticket) for ticket in created)
    solved_counts = Counter(_product_code(ticket) for ticket in solved)
    backlog_counts = Counter(_product_code(ticket) for ticket in backlog)
    rating_counts = defaultdict(lambda: {"good": 0, "bad": 0})
    for rating in ratings:
        rating_counts[_product_code(rating.ticket)][rating.score] += 1

    completed_ids = defaultdict(set)
    achieved_ids = defaultdict(set)
    breached_ids = defaultdict(set)
    for row in sla_instances:
        code = _product_code(row["ticket"])
        if row["status"] == "completed":
            completed_ids[code].add(row["ticket"].id)
            if row["result"] == "achieved":
                achieved_ids[code].add(row["ticket"].id)
        if row["result"] == "breached":
            breached_ids[code].add(row["ticket"].id)

    catalog = list(ProductLine.objects.filter(active=True).order_by("sort_order", "name"))
    product_definitions = [
        {"code": product.code, "name": product.name, "color": product.color, "icon": product.icon}
        for product in catalog
    ]
    unclassified_definition = {
        "code": "unclassified", "name": "Sin clasificar", "color": "#b45309",
        "icon": "fa-triangle-exclamation",
    }
    if filters.unclassified_product:
        definitions = [unclassified_definition]
    elif filters.product_line_id:
        selected_codes = {product.code for product in catalog if product.id == filters.product_line_id}
        definitions = [row for row in product_definitions if row["code"] in selected_codes]
    else:
        definitions = product_definitions + [unclassified_definition]
    rows = []
    for definition in definitions:
        code = definition["code"]
        good = rating_counts[code]["good"]
        bad = rating_counts[code]["bad"]
        clean_achieved = achieved_ids[code] - breached_ids[code]
        rows.append({
            **definition,
            "created": created_counts[code],
            "resolved": solved_counts[code],
            "backlog": backlog_counts[code],
            "sla": _pct(len(clean_achieved), len(completed_ids[code])),
            "csat": _pct(good, good + bad),
            "ratings": good + bad,
        })

    all_good = sum(1 for rating in ratings if rating.score == "good")
    all_completed_ids = {row["ticket"].id for row in completed}
    all_achieved_ids = {row["ticket"].id for row in completed if row["result"] == "achieved"}
    all_breached_ids = {row["ticket"].id for row in sla_instances if row["result"] == "breached"}
    payload = _base_payload("executive", date_from, date_to)
    payload["kpis"] = [
        _kpi("created", "Tickets recibidos", len(created)),
        _kpi("resolved", "Tickets resueltos", len(solved)),
        _kpi("backlog", "Pendientes al cierre", len(backlog)),
        _kpi("sla", "Cumplimiento SLA", _pct(len(all_achieved_ids - all_breached_ids), len(all_completed_ids)), "%"),
        _kpi("csat", "Satisfacción", _pct(all_good, len(ratings)), "%", f"{len(ratings)} valoraciones"),
    ]
    payload["matrix"] = rows
    payload["charts"] = {
        "volume_by_product": [
            {"label": row["name"], "created": row["created"], "resolved": row["resolved"], "backlog": row["backlog"]}
            for row in rows
        ],
        "quality_by_product": [
            {"label": row["name"], "sla": row["sla"], "csat": row["csat"], "ratings": row["ratings"]}
            for row in rows
        ],
    }
    return payload


def _previous_period(date_from, date_to):
    days = (date_to - date_from).days + 1
    previous_to = date_from - timedelta(days=1)
    previous_from = previous_to - timedelta(days=days - 1)
    tz = timezone.get_current_timezone()
    previous_start = timezone.make_aware(datetime.combine(previous_from, time.min), tz)
    previous_end = timezone.make_aware(datetime.combine(previous_to, time.max), tz)
    return previous_from, previous_to, previous_start, previous_end


def _delta_fields(current, previous):
    if current is None or previous is None:
        return {"previous": previous, "delta": None, "delta_pct": None, "direction": "none"}
    delta = round(float(current) - float(previous), 1)
    if delta.is_integer():
        delta = int(delta)
    delta_pct = round(delta / abs(float(previous)) * 100, 1) if previous else None
    return {
        "previous": previous,
        "delta": delta,
        "delta_pct": delta_pct,
        "direction": "up" if delta > 0 else "down" if delta < 0 else "flat",
    }


def _attach_comparison(payload, previous, previous_from, previous_to):
    previous_kpis = {row["key"]: row for row in previous.get("kpis", [])}
    for kpi in payload.get("kpis", []):
        old = previous_kpis.get(kpi["key"], {})
        kpi.update(_delta_fields(kpi.get("value"), old.get("value")))

    if payload.get("matrix"):
        previous_rows = {row["code"]: row for row in previous.get("matrix", [])}
        for row in payload["matrix"]:
            old = previous_rows.get(row["code"], {})
            row["comparison"] = {
                key: _delta_fields(row.get(key), old.get(key))
                for key in ("created", "resolved", "backlog", "sla", "csat")
            }
    payload["comparison"] = {"from": str(previous_from), "to": str(previous_to)}
    first = next((kpi for kpi in payload.get("kpis", []) if kpi.get("delta") is not None), None)
    if first:
        sign = "+" if first["delta"] > 0 else ""
        payload["insight"] = (
            f"{first['label']}: {sign}{first['delta']} frente al periodo anterior "
            f"({previous_from} a {previous_to})."
        )
    else:
        payload["insight"] = "No hay muestra comparable suficiente en el periodo anterior."


SECTION_BUILDERS = {
    "executive": build_executive,
    "tickets": build_tickets,
    "efficiency": build_efficiency,
    "assignee_activity": build_assignee_activity,
    "agent_updates": build_agent_updates,
    "unsolved": build_unsolved,
    "backlog": build_backlog,
    "satisfaction": build_satisfaction,
    "sla": build_sla,
}


def build_reporting_section(section, filters, date_from, date_to, start_dt, end_dt):
    builder = SECTION_BUILDERS.get(section)
    if not builder:
        raise ValueError(f"Zona de reporting desconocida: {section}")
    payload = builder(filters, date_from, date_to, start_dt, end_dt)
    previous_from, previous_to, previous_start, previous_end = _previous_period(date_from, date_to)
    previous = builder(
        filters, previous_from, previous_to, previous_start, previous_end
    )
    _attach_comparison(payload, previous, previous_from, previous_to)
    return payload
