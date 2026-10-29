"""
Definition of views.
"""
# -*- coding: utf-8 -*-
from django.db import transaction
from django.db.models import Q, OuterRef, Subquery, Count, Max, Case, When, IntegerField, F
from django.db.models.functions import Coalesce
from django.utils.dateparse import parse_datetime
from datetime import datetime, timedelta
from collections import deque
from types import new_class
from unicodedata import category
from django.shortcuts import render, get_object_or_404, redirect
from django.http import HttpResponse, JsonResponse
from .models import Brand, Organization, ProductLine, Ticket, Comment, User, TicketTag, Attachment, TicketEvent, Notification, Role, Group, Macro, SatisfactionRating, SatisfactionReason, AssignmentRule, AssignmentRuleMember, SLAPolicy, AutomationRule, ResponseTemplate, OutboundEmailLog
from .forms import TicketForm, CommentForm, UserForm
from django.contrib.auth.hashers import check_password
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login as auth_login
from django.contrib.auth import logout
from django.views.decorators.csrf import ensure_csrf_cookie
import json
import logging
import random
from django.views.decorators.http import require_POST
from django.utils.text import slugify
from django.conf import settings
from django.conf import settings as DJANGO_SETTINGS
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET
from django.core.cache import cache
from .api import APIValidationError, api_login_required, json_error, json_ok, parse_json_body
from .constants import CHANNEL_CHOICES, SERVICE_CHOICES, SLA_AT_RISK_WINDOW_MINUTES, TICKET_TYPE_CHOICES
from .permissions import agent_user_filter, can_manage_users, can_update_ticket, can_view_all_tickets, can_view_ticket, is_admin as permission_is_admin, is_agent as permission_is_agent, is_readonly
from .sanitizers import sanitize_email_html
from .services.conversation import prepare_comments
from .services.assignment import AssignmentService
from .services.attachments import AttachmentService
from .services.comments import CommentService
from .services.merge import MergeService
from .services.notifications import NotificationService
from .services.satisfaction import SatisfactionService
from .services.sla import SLAService
from .services.tickets import TicketService
from .services.ticket_status import SOLVED_STATUSES

# En las colas de trabajo, on-hold sigue visible hasta su cierre definitivo.
QUEUE_FINISHED_STATUSES = ("resolved", "closed")
from .services.ticket_trash import TicketTrashService
from .services.presence import heartbeat as ticket_presence_heartbeat
from .services.user_admin import UserAdminService

logger = logging.getLogger('app.views')

# In-memory cache for the full user list (changes rarely, 60s TTL).
# Each worker process keeps its own copy — no cross-process invalidation needed.
import threading as _threading
import time as _time

_USERS_CACHE: dict = {'data': None, 'ts': 0.0}
_USERS_LOCK = _threading.Lock()
_USERS_TTL = 60  # seconds


def _cached_all_users():
    now = _time.monotonic()
    with _USERS_LOCK:
        if _USERS_CACHE['data'] is None or now - _USERS_CACHE['ts'] > _USERS_TTL:
            _USERS_CACHE['data'] = list(
                User.objects.filter(is_active=True).only('id', 'name', 'email').order_by('name')
            )
            _USERS_CACHE['ts'] = now
        return _USERS_CACHE['data']


_DEFAULT_PAGE_SIZE = 50
_ALLOWED_PAGE_SIZES = {10, 20, 50, 100, 150}
_TICKET_FILTER_COUNTS_TTL = 15

# Las colas muestran trabajo vivo aunque el ticket se crease hace meses.
_TICKET_QUEUE_VIEWS = {
    'unsolved_no_tareas', 'all_unsolved_no_tareas', 'unassigned', 'pendientes',
    'unsolved_groups', 'internos_comuny', 'abiertos_ecomfax', 'recordia_sgsd',
    'sus_pendientes', 'espera', 'abiertos', 'sus_no_cerrados', 'no_resueltos',
    'new_in_groups', 'open', 'no_update_48h', 'sla_breached', 'sla_at_risk', 'trash',
}

_TICKET_SORT_FIELDS = {
    'id':         'id',
    'subject':    'subject',
    'requester':  'requester__name',
    'updated_at': 'updated_at',
    'service':    'service',
    'assignee':   'assignee__name',
    'brand':      'brand__name',
    'channel':    'channel',
    'priority':   'priority',
    'sla':        'sla_next_due',  # requiere SLAService.annotate_urgency (se aplica en filter_tickets)
}

# Subfiltro (agrupación) por vista, replicando el comportamiento de Zendesk
_TICKET_GROUP_BY = {
    'mis_tickets':            'status',
    'telefonica_mes':         'assignee',
    'unsolved_no_tareas':     'status',
    'unassigned':             'status',
    'all_unsolved_no_tareas': 'assignee',
    'recently_updated':       'status',
    'recently_solved':        'assignee',
    'pendientes':             'assignee',
    'tareas':                 'assignee',
    'sus_tareas':             'status',
    'unsolved_groups':        'group',
    'rated_last7':            'assignee',
    'internos_comuny':        'assignee',
    'abiertos_ecomfax':       'assignee',
    'recordia_sgsd':          'assignee',
    'closed':                 'requester',
    'sus_pendientes':         'status',
    'espera':                 'assignee',
    'abiertos':               'assignee',
    'sus_no_cerrados':        'status',
    'ultimos_cerrados':       'requester',
    'no_resueltos':           'status',
    'twitter':                'status',
    'twitter_dm':             'status',
    'twitter_like':           'status',
    'resueltos':              'requester',
    'new_in_groups':          'group',
    'open':                   'assignee',
    'no_update_48h':          'assignee',
    'sla_breached':           'assignee',
    'sla_at_risk':            'assignee',
}

_TICKET_VIEW_LABELS = {
    'trash': 'Tickets eliminados',
    'mis_tickets': 'Todos tus tickets',
    'telefonica_mes': 'Tickets Telefónica último mes',
    'unsolved_no_tareas': 'Tus tickets no resueltos (sin tareas)',
    'unassigned': 'Tickets sin asignar',
    'all_unsolved_no_tareas': 'Todos los tickets no resueltos (sin tareas)',
    'recently_updated': 'Tickets actualizados recientemente',
    'recently_solved': 'Tickets resueltos recientemente',
    'pendientes': 'Tickets en estado pending',
    'tareas': 'Todas las tareas',
    'unsolved_groups': 'Tickets no resueltos de tus grupos',
    'rated_last7': 'Tickets valorados en los últimos 7 días',
    'internos_comuny': 'Tickets internos open de Comunycarse',
    'abiertos_ecomfax': 'Tickets open de eComFax',
    'recordia_sgsd': 'Recordia SGSD',
    'closed': 'Tickets en estado closed',
    'sus_pendientes': 'Sus tickets en estado pending',
    'espera': 'Tickets en estado espera',
    'abiertos': 'Tickets en estado open',
    'sus_no_cerrados': 'Sus tickets no closed',
    'ultimos_cerrados': 'Últimos tickets closed',
    'no_resueltos': 'Todos los tickets no resueltos',
    'twitter': 'Recibido por Twitter',
    'twitter_dm': 'Recibido por Twitter mensaje directo',
    'twitter_like': 'Recibido por Twitter Me gusta',
    'sus_tareas': 'Todas sus tareas',
    'resueltos': 'Tickets en estado resolved',
    'new_in_groups': 'Tickets nuevos de tus grupos',
    'open': 'Tickets en estado open',
    'no_update_48h': 'Tickets sin actualizar en 48 horas',
    'sla_breached': 'SLA incumplido',
    'sla_at_risk': 'SLA en riesgo',
}

_GROUP_DB_SORT = {
    'assignee':  'assignee__name',
    'status':    'status',
    'group':     'assigned_group__group_name',
    'requester': 'requester__name',
}

_CUSTOMER_SORT_FIELDS = {
    'id':         'id',
    'name':       'name',
    'email':      'email',
    'status':     'group__group_name',
    'role':       'role__role_name',
    'group':      'group__group_name',
    'created_at': 'created_at',
}

_PROFILE_STATUS_FILTERS = {
    'all':     None,
    'open':    {'exclude': SOLVED_STATUSES},
    'pending': {'filter': ['pending']},
    'solved':  {'filter': SOLVED_STATUSES},
}


def _profile_tickets_context(request, base_qs):
    """Paginated + filtered ticket section for profile pages.

    Avoids loading thousands of tickets and N+1 comment lookups: we only
    materialize the current page and attach last-comments to those rows.
    """
    status = (request.GET.get('status') or 'all').lower()
    if status not in _PROFILE_STATUS_FILTERS:
        status = 'all'
    q = (request.GET.get('q') or '').strip()
    page_size = _parse_page_size(request.GET.get('page_size'))
    try:
        page = max(1, int(request.GET.get('page') or 1))
    except (ValueError, TypeError):
        page = 1

    qs = base_qs
    rule = _PROFILE_STATUS_FILTERS[status]
    if rule:
        if 'filter' in rule:
            qs = qs.filter(status__in=rule['filter'])
        if 'exclude' in rule:
            qs = qs.exclude(status__in=rule['exclude'])

    if q:
        if q.startswith('#') and q[1:].isdigit():
            qs = qs.filter(id=int(q[1:]))
        elif q.isdigit():
            qs = qs.filter(Q(id=int(q)) | Q(subject__icontains=q))
        else:
            qs = qs.filter(subject__icontains=q)

    qs = qs.select_related('requester', 'assignee').order_by('-updated_at')

    total = qs.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    if page > total_pages:
        page = total_pages
    start = (page - 1) * page_size
    tickets = list(qs[start:start + page_size])
    _attach_last_comments(tickets)

    return {
        'tickets': tickets,
        'pagination': {
            'page': page,
            'page_size': page_size,
            'total': total,
            'total_pages': total_pages,
            'has_prev': page > 1,
            'has_next': page < total_pages,
            'prev_page': page - 1,
            'next_page': page + 1,
            'start_index': 0 if total == 0 else start + 1,
            'end_index': min(start + page_size, total),
        },
        'filters': {'status': status, 'q': q},
        'page_sizes': sorted(_ALLOWED_PAGE_SIZES),
    }


try:
    import bleach
    _HAS_BLEACH = True
except ImportError:  # pragma: no cover - safety net for envs without bleach
    _HAS_BLEACH = False

_ALLOWED_HTML_TAGS = [
    'a', 'b', 'blockquote', 'br', 'code', 'div', 'em', 'h1', 'h2', 'h3',
    'h4', 'h5', 'h6', 'hr', 'i', 'img', 'li', 'ol', 'p', 'pre', 's',
    'span', 'strike', 'strong', 'sub', 'sup', 'table', 'tbody', 'td',
    'tfoot', 'th', 'thead', 'tr', 'u', 'ul',
]
_ALLOWED_HTML_ATTRS = {
    '*': ['class', 'style', 'title'],
    'a': ['href', 'target', 'rel'],
    'img': ['src', 'alt', 'width', 'height'],
    'td': ['colspan', 'rowspan'],
    'th': ['colspan', 'rowspan'],
}
_ALLOWED_HTML_PROTOCOLS = ['http', 'https', 'mailto', 'tel', 'data']


def _sanitize_email_html(html):
    """Sanitize untrusted HTML coming from email bodies or the rich-text composer.

    Allow-list approach: keeps formatting and email-friendly tables, strips
    scripts / event handlers / unknown protocols.
    """
    return sanitize_email_html(html)


def _attach_last_comments(ticket_list):
    if not ticket_list:
        return
    ids = [t.id for t in ticket_list]
    # Get the highest comment ID per ticket (IDs are monotonically increasing,
    # so MAX(id) == latest comment without needing to sort by created_at).
    # Two fast indexed queries instead of a 4k-row filesort.
    latest_ids = list(
        Comment.objects
        .filter(ticket_id__in=ids)
        .values('ticket_id')
        .annotate(last_id=Max('id'))
        .values_list('last_id', flat=True)
    )
    lc_map = {}
    for c in Comment.objects.filter(id__in=latest_ids).select_related('user'):
        lc_map[c.ticket_id] = c
    for t in ticket_list:
        lc = lc_map.get(t.id)
        t.lc_author   = lc.user.name if lc and lc.user else ''
        t.lc_date     = timezone.localtime(lc.created_at).strftime('%d/%m/%Y %H:%M') if lc else ''
        t.lc_body     = (lc.content or '')[:300] if lc else ''
        t.lc_internal = bool(lc and not lc.is_public)


def _parse_page_size(raw):
    try:
        size = int(raw or _DEFAULT_PAGE_SIZE)
        return size if size in _ALLOWED_PAGE_SIZES else _DEFAULT_PAGE_SIZE
    except (ValueError, TypeError):
        return _DEFAULT_PAGE_SIZE


def _listing_assignees(tickets):
    """Todos los asignados presentes en la cola, incluidas las filas sin asignar."""
    rows = tickets.order_by().values('assignee_id', 'assignee__name', 'assignee__email').annotate(
        total=Count('id'),
    ).order_by(F('assignee__name').asc(nulls_last=True), 'assignee_id')
    return [{'id': row['assignee_id'], 'name': row['assignee__name'] or row['assignee__email'] or 'Sin asignar',
             'email': row['assignee__email'] or '', 'total': row['total']} for row in rows]


def _balanced_agent_page(agent_counts, page, page_size):
    """
    Calcula el tramo de cada agente mediante round-robin estable.

    El cursor continúa entre páginas: los restos rotan, un agente agotado deja
    su hueco a los demás y, si hay más agentes que filas, la página siguiente
    continúa por el siguiente agente en vez de favorecer siempre a los primeros.
    """
    counts = [(agent_id, max(0, int(total))) for agent_id, total in agent_counts if total]
    total = sum(total for _, total in counts)
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(max(1, page), total_pages)
    if not counts:
        return page, total_pages, total, {}, {}

    remaining = {agent_id: count for agent_id, count in counts}
    consumed = {agent_id: 0 for agent_id, _ in counts}
    queue = deque(agent_id for agent_id, _ in counts)
    target_offsets = {}
    target_allocations = {}

    for current_page in range(1, page + 1):
        if current_page == page:
            target_offsets = consumed.copy()
        allocation = {agent_id: 0 for agent_id, _ in counts}
        slots = min(page_size, sum(remaining.values()))
        for _ in range(slots):
            agent_id = queue.popleft()
            remaining[agent_id] -= 1
            consumed[agent_id] += 1
            allocation[agent_id] += 1
            if remaining[agent_id] > 0:
                queue.append(agent_id)
        if current_page == page:
            target_allocations = {agent_id: amount for agent_id, amount in allocation.items() if amount}

    return page, total_pages, total, target_offsets, target_allocations


def _ticket_filter_counts_cache_key(user, brand_id=None, date_from=None, date_to=None, date_mode=None):
    role_name = (getattr(getattr(user, 'role', None), 'role_name', '') or '').lower()
    return (
        f"tickets:filter-counts:v6:user:{user.id}:"
        f"role:{role_name}:group:{user.group_id or 0}:brand:{brand_id or 0}:"
        f"from:{date_from}:to:{date_to}:mode:{date_mode or 'default'}"
    )


def _parse_filter_date_range(params, default_days=30, max_days=366):
    """Devuelve un rango local inclusivo y normalizado para filtros de tickets."""
    today = timezone.localdate()
    try:
        date_from = datetime.strptime(params.get('from', ''), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        date_from = today - timedelta(days=default_days)
    try:
        date_to = datetime.strptime(params.get('to', ''), '%Y-%m-%d').date()
    except (ValueError, TypeError):
        date_to = today
    if date_to < date_from:
        date_from, date_to = date_to, date_from
    if (date_to - date_from).days > max_days:
        date_from = date_to - timedelta(days=max_days)

    tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(date_from, datetime.min.time()), tz)
    end_dt = timezone.make_aware(datetime.combine(date_to, datetime.max.time()), tz)
    return date_from, date_to, start_dt, end_dt


def _view_uses_date_range(view, params=None):
    # Las colas incluyen todo por defecto; el usuario puede acotar cualquier
    # vista explícitamente, o quitar el límite de un histórico.
    mode = (params or {}).get('date_mode')
    if mode in ('all', 'range'):
        return mode == 'range'
    return not (view in _TICKET_QUEUE_VIEWS or (view or '').startswith('home_'))


def _ticket_listing_queryset(request, *, view=None):
    """Query base de /tickets con empresa y fechas aplicadas una sola vez."""
    view = (request.GET.get('view') or '') if view is None else view
    if view == 'trash':
        base_qs = Ticket.objects.filter(is_deleted=True, merged_into__isnull=True) if _can_view_all(request.user) else Ticket.objects.none()
    else:
        base_qs = _base_tickets_queryset(request.user)
    brand_id = _as_int_or_none(request.GET.get('brand_id'))
    date_from, date_to, start_dt, end_dt = _parse_filter_date_range(request.GET)
    if brand_id:
        base_qs = base_qs.filter(brand_id=brand_id)
    if _view_uses_date_range(view, request.GET):
        base_qs = base_qs.filter(created_at__range=(start_dt, end_dt))
    return base_qs, brand_id, date_from, date_to


def _ticket_filter_payload(brand_id, date_from, date_to, view=None, params=None):
    return {
        'brand_id': brand_id,
        'from': str(date_from),
        'to': str(date_to),
        'view': view or '',
        'view_label': _TICKET_VIEW_LABELS.get(view, 'Todos los tickets'),
        'date_range_applied': _view_uses_date_range(view, params),
    }


def _ensure_urgency(qs):
    """Anota sla_next_due si el queryset no lo trae ya (la vista sla_at_risk lo anota)."""
    if 'sla_next_due' in qs.query.annotations:
        return qs
    return SLAService.annotate_urgency(qs)


def _base_tickets_queryset(user):
    qs = Ticket.objects.filter(merged_into__isnull=True, is_deleted=False)
    if _can_view_all(user):
        return qs.order_by('-updated_at')
    return qs.filter(Q(requester=user) | Q(ccs=user)).distinct().order_by('-updated_at')


def _my_tickets_queryset(base_qs, user):
    if _can_view_all(user):
        return base_qs.filter(assignee=user)
    return base_qs.filter(Q(requester=user) | Q(ccs=user)).distinct()


def _tickets_for_view(base_qs, view, user):
    now = timezone.now()
    if view == 'trash':
        return base_qs.filter(is_deleted=True).annotate(
            trash_date=Coalesce('deleted_at', 'updated_at'),
        ).order_by('-trash_date', '-id')

    # --- Vistas del dashboard (home) -------------------------------------
    # Siempre acotadas al usuario: _base_tickets_queryset devuelve TODOS los
    # tickets cuando quien mira es agente, y la home solo debe enseñar los
    # suyos. Cada clave replica la definición del contador que hay bajo el
    # botón correspondiente en home(), para que el número del botón y las
    # filas que salen al pulsarlo coincidan.
    if view and view.startswith("home_"):
        mine = _my_tickets_queryset(base_qs, user)
        cutoff_60 = now - timedelta(days=60)
        if view == "home_you":
            return mine.filter(status="open")
        if view == "home_pending":
            return mine.filter(status="pending")
        if view == "home_hold":
            return mine.filter(status="on_hold")
        if view == "home_groups":
            # Ojo: el contador abarca todo el grupo, no solo lo del usuario.
            if not user.group_id:
                return base_qs.none()
            return base_qs.filter(assignee__group_id=user.group_id, status="open")
        if view == "home_bien":
            return mine.filter(
                satisfaction_ratings__score="good",
                satisfaction_ratings__created_at__gte=cutoff_60,
            ).distinct()
        if view == "home_mal":
            return mine.filter(
                satisfaction_ratings__score="bad",
                satisfaction_ratings__created_at__gte=cutoff_60,
            ).distinct()
        if view == "home_satisfaccion":
            return mine.filter(
                satisfaction_ratings__score__in=("good", "bad"),
                satisfaction_ratings__created_at__gte=cutoff_60,
            ).distinct()
        if view == "home_solventado":
            return mine.filter(
                status__in=SOLVED_STATUSES, updated_at__gte=cutoff_60
            )
        # home_all replica «Tus tickets no resueltos (sin tareas)».
        return mine.exclude(status__in=QUEUE_FINISHED_STATUSES).exclude(type='task')

    if view == "mis_tickets":
        return base_qs
    if view == "telefonica_mes":
        return base_qs.filter(
            requester__organization__name="Telefonica",
            created_at__gte=now - timedelta(days=30),
        )
    if view == "unsolved_no_tareas":
        return _my_tickets_queryset(base_qs, user).filter(~Q(status__in=QUEUE_FINISHED_STATUSES), ~Q(type="task"))
    if view == "unassigned":
        return base_qs.filter(assignee__isnull=True)
    if view == "all_unsolved_no_tareas":
        return base_qs.filter(
            ~Q(status__in=QUEUE_FINISHED_STATUSES),
            ~Q(type="task"),
        )
    if view == "recently_updated":
        return base_qs.filter(updated_at__gte=now - timedelta(hours=24))
    if view == "recently_solved":
        return base_qs.filter(status="resolved")
    if view == "pendientes":
        return base_qs.filter(status="pending")
    if view == "tareas":
        return base_qs.filter(type="task")
    if view == "unsolved_groups":
        return base_qs.filter(~Q(status__in=QUEUE_FINISHED_STATUSES), assignee__group=user.group)
    if view == "rated_last7":
        since = now - timedelta(days=7)
        rated_ids = SatisfactionRating.objects.filter(
            created_at__gte=since, score__in=['good', 'bad']
        ).values_list('ticket_id', flat=True)
        return base_qs.filter(id__in=rated_ids)
    if view == "internos_comuny":
        return base_qs.filter(service="internocomuny", status="open")
    if view == "abiertos_ecomfax":
        return base_qs.filter(service="ecomfax", status="open")
    if view == "recordia_sgsd":
        return base_qs.filter(service="recordia", status="open")
    if view == "closed":
        return base_qs.filter(status="closed")
    if view == "sus_pendientes":
        return base_qs.filter(requester=user, status="pending")
    if view == "espera":
        return base_qs.filter(status="on_hold")
    if view == "abiertos":
        return base_qs.filter(status="open")
    if view == "sus_no_cerrados":
        if _can_view_all(user):
            return base_qs.filter(requester=user).exclude(status="closed")
        return base_qs.exclude(status="closed")
    if view == "ultimos_cerrados":
        return base_qs.filter(status="closed")
    if view == "no_resueltos":
        return base_qs.exclude(status__in=QUEUE_FINISHED_STATUSES)
    if view == "twitter":
        return base_qs.filter(channel="twitter")
    if view == "twitter_dm":
        return base_qs.filter(channel="twitter_dm")
    if view == "twitter_like":
        return base_qs.filter(channel="twitter_like")
    if view == "sus_tareas":
        return base_qs.filter(requester=user, type="task")
    if view == "resueltos":
        return base_qs.filter(status="resolved")
    if view == "new_in_groups":
        return base_qs.filter(assignee__group=user.group, created_at__gte=now - timedelta(days=7))
    if view == "open":
        return base_qs.filter(status="open")
    if view == "no_update_48h":
        return base_qs.filter(updated_at__lte=now - timedelta(hours=48))
    if view == "sla_breached":
        return base_qs.filter(sla_breached_at__isnull=False).exclude(status__in=SOLVED_STATUSES)
    if view == "sla_at_risk":
        return SLAService.annotate_urgency(
            base_qs.filter(sla_breached_at__isnull=True).exclude(status__in=SOLVED_STATUSES)
        ).filter(
            sla_next_due__gt=now,
            sla_next_due__lte=now + timedelta(minutes=SLA_AT_RISK_WINDOW_MINUTES),
        )
    return base_qs


def _home_stats(user):
    # Mismo universo que la tabla (_base_tickets_queryset): sin fusionados ni
    # eliminados, o el número del botón no cuadra con las filas al pulsarlo.
    live = Ticket.objects.filter(merged_into__isnull=True, is_deleted=False)
    tickets_qs = _my_tickets_queryset(_base_tickets_queryset(user), user)

    # --- Tickets Abiertos ---
    abiertos_you = tickets_qs.filter(status="open").count()
    abiertos_groups = 0
    if user.group:
        abiertos_groups = live.filter(
            assignee__group=user.group, status="open"
        ).distinct().count()

    cutoff_60  = timezone.now() - timedelta(days=60)
    solventado = tickets_qs.filter(
        status__in=SOLVED_STATUSES,
        updated_at__gte=cutoff_60,
    ).count()

    # La tabla ya no se pinta en servidor: la rellena home_tickets.js desde
    # /tickets/filter/?view=home_* con paginación y orden, igual que /tickets/.
    # Aquí solo hace falta el total para el encabezado, y el propio JS lo
    # refresca con el que devuelve la API en cada carga.
    pendientes_count = tickets_qs.exclude(status__in=QUEUE_FINISHED_STATUSES).exclude(type='task').count()

    # Personal bien/mal: tickets del usuario, últimos 60 días
    user_ratings_60 = SatisfactionRating.objects.filter(
        ticket__in=tickets_qs,
        created_at__gte=cutoff_60,
        score__in=['good', 'bad'],
    )
    bien = user_ratings_60.filter(score='good').count()
    mal  = user_ratings_60.filter(score='bad').count()

    total_rated = bien + mal
    satisfaccion_pct = round((bien / total_rated) * 100) if total_rated > 0 else None

    return {
        "pendientes_count": pendientes_count,
        "stats": {
            "abiertos_you": abiertos_you,
            "abiertos_groups": abiertos_groups,
            "bien": bien,
            "mal": mal,
            "solventado": solventado,
            "satisfaccion_pct": satisfaccion_pct,
            "total_rated": total_rated,
        },
    }


@login_required
def home(request):
    user = request.user
    context = {
        "username": user.name,
        "email": user.email,
        "current_user_id": user.id,
        "current_user_group_id": user.group_id,
        **_home_stats(user),
    }
    return render(request, "tickets/home.html", context)


@api_login_required
def home_stats_api(request):
    return JsonResponse(_home_stats(request.user))

@login_required
def tickets_list(request):
    # Los filtros se cargan de forma asíncrona desde filter_tickets?counts=1
    brands = list(Brand.objects.exclude(name='').order_by('name').values('id', 'name'))
    return render(request, "tickets/tickets_list.html", {"brands": brands})

@api_login_required
def filter_tickets(request):
    """
    Devuelve tickets paginados en JSON según el 'view' seleccionado.
    Parámetros: view, page (1-based), counts=1 (opcional, activa el cálculo de contadores).
    """
    view = request.GET.get("view")
    if view == 'trash' and not _can_view_all(request.user):
        return json_error('forbidden', 'Solo agentes pueden ver la papelera.', status=403)
    compute_counts = request.GET.get("counts", "0") == "1"
    counts_only = request.GET.get("counts_only", "0") == "1"
    force_counts = request.GET.get("force_counts", "0") == "1"
    fast_pagination = request.GET.get("fast", "0") == "1"
    total_only = request.GET.get("total_only", "0") == "1"
    sort_by  = request.GET.get("sort_by", "")
    sort_dir = request.GET.get("sort_dir", "asc") if request.GET.get("sort_dir") in ("asc", "desc") else "asc"
    try:
        page = max(1, int(request.GET.get("page", 1) or 1))
    except (ValueError, TypeError):
        page = 1
    page_size = _parse_page_size(request.GET.get("page_size"))

    # Empresa y fechas se aplican al queryset base para que vistas, contadores,
    # totales, paginación y exportaciones partan del mismo conjunto exacto.
    base_qs, brand_id, date_from, date_to = _ticket_listing_queryset(request)
    applied_filters = _ticket_filter_payload(brand_id, date_from, date_to, view, request.GET)

    if total_only:
        total = _tickets_for_view(base_qs, view, request.user).count()
        return JsonResponse({
            "pagination": {
                "total": total,
                "total_pages": max(1, (total + page_size - 1) // page_size),
                "page_size": page_size,
            },
            "filtros": {view: total} if view else {},
            "applied_filters": applied_filters,
        })

    # Los contadores son costosos. Solo se calculan cuando counts=1.
    # Consolidamos ~25 COUNTs separados en 5 queries para reducir round-trips.
    filtros = {}
    _counts_cache_key = None
    _counts_loaded_from_cache = False
    if compute_counts:
        _counts_cache_key = _ticket_filter_counts_cache_key(
            request.user, brand_id, date_from, date_to, request.GET.get('date_mode')
        )
        if not force_counts:
            cached_filtros = cache.get(_counts_cache_key)
            if cached_filtros is not None:
                filtros = cached_filtros
                compute_counts = False
                _counts_loaded_from_cache = True

    if compute_counts:
        _now = timezone.now()
        # El lateral mezcla colas e históricos. Sus contadores son independientes
        # de la vista activa. Un rango explícito se aplica a todas las vistas.
        queue_qs = _ticket_listing_queryset(request, view='unassigned')[0]
        ranged_qs = _ticket_listing_queryset(request, view='mis_tickets')[0]
        _, _, start_dt, end_dt = _parse_filter_date_range(request.GET)
        in_range = Q(created_at__range=(start_dt, end_dt)) if _view_uses_date_range('mis_tickets', request.GET) else Q()
        mine = Q(assignee=request.user) if _can_view_all(request.user) else Q()

        # Query 1: aggregate condicional — una sola pasada sobre la tabla base
        # cubre todos los contadores basados en status/type/channel/service/assignee.
        _user_group = request.user.group
        _agg = queue_qs.aggregate(
            mis_tickets      = Count('id', filter=in_range),
            n_open           = Count(Case(When(status='open',     then=1), output_field=IntegerField())),
            n_pending        = Count(Case(When(status='pending',  then=1), output_field=IntegerField())),
            n_closed         = Count('id', filter=in_range & Q(status='closed')),
            n_resolved       = Count('id', filter=in_range & Q(status='resolved')),
            n_tarea          = Count('id', filter=in_range & Q(type='task')),
            n_twitter        = Count('id', filter=in_range & Q(channel='twitter')),
            n_twitter_dm     = Count('id', filter=in_range & Q(channel='twitter_dm')),
            n_twitter_like   = Count('id', filter=in_range & Q(channel='twitter_like')),
            n_on_hold        = Count('id', filter=Q(status='on_hold')),
            n_mine_unsolved  = Count('id', filter=mine & ~Q(status__in=QUEUE_FINISHED_STATUSES) & ~Q(type='task')),
            n_comuny         = Count(Case(When(service='internocomuny',  status='open', then=1), output_field=IntegerField())),
            n_ecomfax        = Count(Case(When(service='ecomfax',        status='open', then=1), output_field=IntegerField())),
            n_recordia       = Count(Case(When(service='recordia',       status='open', then=1), output_field=IntegerField())),
            n_unassigned     = Count(Case(When(assignee__isnull=True,    then=1), output_field=IntegerField())),
            n_unsolved_notarea = Count(Case(
                When(~Q(status__in=QUEUE_FINISHED_STATUSES) & ~Q(type='task'), then=1),
                output_field=IntegerField()
            )),
            n_no_resueltos   = Count('id', filter=~Q(status__in=QUEUE_FINISHED_STATUSES)),
            n_sla_breached   = Count(Case(
                When(Q(sla_breached_at__isnull=False) & ~Q(status__in=SOLVED_STATUSES), then=1),
                output_field=IntegerField()
            )),
        )

        # SLA en riesgo requiere la anotación sla_next_due — query propia pequeña.
        n_sla_at_risk = _ensure_urgency(
            queue_qs.filter(sla_breached_at__isnull=True).exclude(status__in=SOLVED_STATUSES)
        ).filter(
            sla_next_due__gt=_now,
            sla_next_due__lte=_now + timedelta(minutes=SLA_AT_RISK_WINDOW_MINUTES),
        ).count()

        # Query 2: contadores dependientes del usuario actual (requester/group)
        _user_agg = queue_qs.filter(requester=request.user).aggregate(
            sus_pendientes = Count(Case(When(status='pending', then=1), output_field=IntegerField())),
            sus_tareas     = Count('id', filter=in_range & Q(type='task')),
            sus_no_cerrados_agent = Count(Case(When(~Q(status='closed'), then=1), output_field=IntegerField())),
        )

        # Query 3: filtros de tiempo (updated_at / created_at ranges)
        _time_agg = queue_qs.aggregate(
            recently_updated = Count('id', filter=in_range & Q(updated_at__gte=_now - timedelta(hours=24))),
            no_update_48h    = Count(Case(When(updated_at__lte=_now - timedelta(hours=48), then=1), output_field=IntegerField())),
        )

        # Query 4: Telefónica (JOIN a organization — no se puede incluir en aggregate base)
        n_telefonica = ranged_qs.filter(
            requester__organization__name="Telefonica",
            created_at__gte=_now - timedelta(days=30),
        ).count()

        # Query 5: grupos del usuario actual (requires group join)
        n_unsolved_groups = 0
        n_new_in_groups   = 0
        if _user_group:
            n_unsolved_groups = queue_qs.filter(
                ~Q(status__in=QUEUE_FINISHED_STATUSES),
                assignee__group=_user_group,
            ).count()
            n_new_in_groups = queue_qs.filter(
                assignee__group=_user_group,
                created_at__gte=_now - timedelta(days=7),
            ).count()

        sus_no_cerrados_count = (
            (queue_qs.exclude(status='closed').count() if not _can_view_all(request.user)
             else _user_agg['sus_no_cerrados_agent'])
        )

        filtros = {
            "mis_tickets":        _agg['mis_tickets'],
            "telefonica_mes":     n_telefonica,
            "unsolved_no_tareas": _agg['n_mine_unsolved'],
            "unassigned":         _agg['n_unassigned'],
            "all_unsolved_no_tareas": _agg['n_unsolved_notarea'],
            "recently_updated":   _time_agg['recently_updated'],
            "recently_solved":    _agg['n_resolved'],
            "pendientes":         _agg['n_pending'],
            "tareas":             _agg['n_tarea'],
            "unsolved_groups":    n_unsolved_groups,
            "rated_last7":        0,
            "internos_comuny":    _agg['n_comuny'],
            "abiertos_ecomfax":   _agg['n_ecomfax'],
            "recordia_sgsd":      _agg['n_recordia'],
            "closed":             _agg['n_closed'],
            "sus_pendientes":     _user_agg['sus_pendientes'],
            "espera":             _agg['n_on_hold'],
            "abiertos":           _agg['n_open'],
            "sus_no_cerrados":    sus_no_cerrados_count,
            "ultimos_cerrados":   _agg['n_closed'],
            "no_resueltos":       _agg['n_no_resueltos'],
            "twitter":            _agg['n_twitter'],
            "twitter_dm":         _agg['n_twitter_dm'],
            "twitter_like":       _agg['n_twitter_like'],
            "sus_tareas":         _user_agg['sus_tareas'],
            "resueltos":          _agg['n_resolved'],
            "new_in_groups":      n_new_in_groups,
            "open":               _agg['n_open'],
            "no_update_48h":      _time_agg['no_update_48h'],
            "sla_breached":       _agg['n_sla_breached'],
            "sla_at_risk":        n_sla_at_risk,
        }

    if filtros and _can_view_all(request.user):
        trash_qs = _ticket_listing_queryset(request, view='trash')[0]
        filtros = {**filtros, 'trash': trash_qs.count()}

    if _counts_cache_key and filtros and not _counts_loaded_from_cache:
        cache.set(_counts_cache_key, filtros, _TICKET_FILTER_COUNTS_TTL)

    if counts_only:
        return JsonResponse({"filtros": filtros, "applied_filters": applied_filters})

    tickets = _tickets_for_view(base_qs, view, request.user)
    balanced_assignment_view = view == 'all_unsolved_no_tareas'

    db_sort = _TICKET_SORT_FIELDS.get(sort_by)
    db_sort_signed = (f'-{db_sort}' if sort_dir == 'desc' else db_sort) if db_sort else None

    # La vista equilibrada conserva siempre sus bloques por agente: la columna
    # elegida ordena dentro del tramo de cada agente, no rompe el reparto.
    if balanced_assignment_view:
        group_by = 'assignee'
        group_sort = None
    # Si el usuario ha pedido ordenar por una columna, desactivamos la agrupación
    # para mostrar la tabla plana ordenada por su criterio en el resto de vistas.
    elif db_sort_signed:
        group_by = None
        group_sort = None
    elif view == 'trash':
        group_by = None
        group_sort = None
    elif fast_pagination:
        group_by = None
        group_sort = None
        tickets = tickets.order_by('-updated_at')
    else:
        group_by = _TICKET_GROUP_BY.get(view)
        group_sort = _GROUP_DB_SORT.get(group_by) if group_by else None

    if group_sort:
        tickets = tickets.order_by(group_sort, '-updated_at')
    elif db_sort_signed and not balanced_assignment_view:
        if sort_by == 'sla':
            # Orden por urgencia de SLA: el próximo vencimiento primero, sin SLA al final.
            tickets = _ensure_urgency(tickets)
            tickets = tickets.order_by(
                F('sla_next_due').desc(nulls_last=True) if sort_dir == 'desc'
                else F('sla_next_due').asc(nulls_last=True)
            )
        else:
            tickets = tickets.order_by(db_sort_signed)

    offset = (page - 1) * page_size
    page_qs = tickets.select_related('requester', 'assignee', 'assigned_group', 'brand')
    if view == 'trash':
        page_qs = page_qs.select_related('deleted_by')

    # La columna "Val." solo existe en la tabla de la home. Se anota únicamente
    # para esas vistas para no meter una subconsulta por fila en /tickets/.
    _want_satisfaction = bool(view and view.startswith("home_"))
    if _want_satisfaction:
        page_qs = page_qs.annotate(
            satisfaction_score=Subquery(
                SatisfactionRating.objects
                .filter(ticket=OuterRef('pk'), score__in=['good', 'bad'])
                .order_by('-created_at')
                .values('score')[:1]
            )
        )

    exact_total = True
    has_next = False

    balanced_distribution = None

    if balanced_assignment_view:
        assignment_agents = _listing_assignees(tickets)
        ticket_counts = {agent['id']: agent['total'] for agent in assignment_agents}
        agent_counts = [
            (agent['id'], ticket_counts[agent['id']])
            for agent in assignment_agents
            if ticket_counts.get(agent['id'], 0) > 0
        ]
        page, total_pages, total, agent_offsets, agent_allocations = _balanced_agent_page(
            agent_counts, page, page_size
        )

        ordered_page_qs = page_qs
        if db_sort_signed:
            if sort_by == 'sla':
                ordered_page_qs = _ensure_urgency(ordered_page_qs)
                ticket_ordering = [
                    F('sla_next_due').desc(nulls_last=True) if sort_dir == 'desc'
                    else F('sla_next_due').asc(nulls_last=True),
                    '-updated_at',
                    '-id',
                ]
            elif sort_by == 'assignee':
                # El agente define el bloque y se mantiene alfabético; dentro
                # del bloque usamos el orden operativo habitual.
                ticket_ordering = ['-updated_at', '-id']
            else:
                ticket_ordering = [db_sort_signed]
                if db_sort != 'id':
                    ticket_ordering.append('id' if sort_dir == 'asc' else '-id')
        else:
            ticket_ordering = ['-updated_at', '-id']

        tickets_page = []
        balanced_distribution = []
        for agent in assignment_agents:
            agent_id = agent['id']
            amount = agent_allocations.get(agent_id, 0)
            if not amount:
                continue
            start = agent_offsets.get(agent_id, 0)
            rows = list(
                ordered_page_qs
                .filter(assignee_id=agent_id)
                .order_by(*ticket_ordering)[start:start + amount]
            )
            tickets_page.extend(rows)
            balanced_distribution.append({
                'agent_id': agent_id,
                'agent': agent['name'] or agent['email'],
                'total': ticket_counts.get(agent_id, 0),
                'offset': start,
                'returned': len(rows),
            })

        exact_total = True
        has_next = page < total_pages
    elif fast_pagination:
        tickets_page = list(page_qs[offset:offset + page_size + 1])

        # If the URL points past the end, fall back to one exact COUNT so the UI
        # can clamp to the real last page. The common first-page path stays
        # count-free and returns after fetching at most page_size + 1 rows.
        if page > 1 and not tickets_page:
            total = tickets.count()
            total_pages = max(1, (total + page_size - 1) // page_size)
            page = min(page, total_pages)
            offset = (page - 1) * page_size
            tickets_page = list(page_qs[offset:offset + page_size])
            has_next = page < total_pages
        elif len(tickets_page) > page_size:
            tickets_page = tickets_page[:page_size]
            total = None
            total_pages = page + 1
            has_next = True
            exact_total = False
        else:
            total = offset + len(tickets_page)
            total_pages = max(1, page)
            has_next = False
    else:
        total = tickets.count()
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size
        tickets_page = list(page_qs[offset:offset + page_size])
        has_next = page < total_pages

    last_comments = {}
    if tickets_page:
        ticket_ids = [t.id for t in tickets_page]
        latest_ids = list(
            Comment.objects
            .filter(ticket_id__in=ticket_ids)
            .values('ticket_id')
            .annotate(last_id=Max('id'))
            .values_list('last_id', flat=True)
        )
        for c in Comment.objects.filter(id__in=latest_ids).select_related('user'):
            last_comments[c.ticket_id] = c

    def _group_value(t):
        if not group_by:
            return None
        if group_by == 'assignee':
            return (t.assignee.name or t.assignee.email) if t.assignee else 'Sin asignar'
        if group_by == 'status':
            return 'On-hold' if t.status == 'on_hold' else (t.status or '—').capitalize()
        if group_by == 'group':
            return t.assigned_group.group_name if t.assigned_group else '— Sin grupo —'
        if group_by == 'requester':
            return t.requester.name if t.requester else '—'
        return None

    _payload_now = timezone.now()
    _risk_window = timedelta(minutes=SLA_AT_RISK_WINDOW_MINUTES)

    def _sla_payload(t):
        dues = [d for d in (t.first_response_due_at, t.resolution_due_at) if d]
        next_due = min(dues) if dues else None
        breached = bool(t.sla_breached_at) and t.status not in SOLVED_STATUSES
        at_risk = bool(
            next_due and not breached
            and t.status not in SOLVED_STATUSES
            and _payload_now < next_due <= _payload_now + _risk_window
        )
        return {
            "next_due": timezone.localtime(next_due).strftime("%d/%m/%Y %H:%M") if next_due else None,
            "breached": breached,
            "at_risk": at_risk,
        }

    data = []
    for t in tickets_page:
        lc = last_comments.get(t.id)
        data.append({
            "id": t.id,
            "subject": t.subject,
            "requester": t.requester.name if t.requester else "-",
            "updated_at": timezone.localtime(t.updated_at).strftime("%d/%m/%Y %H:%M"),
            "service": t.service or "-",
            "assignee": t.assignee.name if t.assignee else "-",
            "status": t.status,
            "priority": t.priority or "",
            "brand": t.brand.name if t.brand else "-",
            "channel": t.channel or "-",
            "type": t.type or "",
            "sla": _sla_payload(t),
            "satisfaction": getattr(t, 'satisfaction_score', None) or "",
            "description": (t.description or "")[:200],
            "deleted_at": timezone.localtime(t.deleted_at).strftime('%d/%m/%Y %H:%M') if t.deleted_at else None,
            "deleted_by": (t.deleted_by.name or t.deleted_by.email) if view == 'trash' and t.deleted_by_id else None,
            "group_value": _group_value(t),
            "last_comment": {
                "author": lc.user.name if lc and lc.user else "",
                "date": timezone.localtime(lc.created_at).strftime("%d/%m/%Y %H:%M") if lc else "",
                "body": (lc.content or "")[:300] if lc else "",
                "is_internal": bool(lc and not lc.is_public),
            } if lc else None,
        })
    return JsonResponse({
        "tickets": data,
        "filtros": filtros,
        "pagination": {
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "page_size": page_size,
            "has_prev": page > 1,
            "has_next": has_next,
            "exact": exact_total,
            "returned": len(data),
        },
        "sort": {"sort_by": sort_by if db_sort else "", "sort_dir": sort_dir},
        "group_by": group_by,
        "applied_filters": applied_filters,
        "balanced_assignment": {
            "enabled": True,
            "agents": balanced_distribution,
        } if balanced_distribution is not None else {"enabled": False, "agents": []},
    })


def _ticket_export_queryset(request):
    """Conjunto y metadatos compartidos por CSV/PDF de la lista de tickets."""
    base_qs, brand_id, date_from, date_to = _ticket_listing_queryset(request)
    view = request.GET.get('view') or 'mis_tickets'
    qs = _tickets_for_view(base_qs, view, request.user)

    sort_by = request.GET.get('sort_by', '')
    sort_dir = request.GET.get('sort_dir', 'asc')
    db_sort = _TICKET_SORT_FIELDS.get(sort_by)
    if db_sort:
        if sort_by == 'sla':
            qs = _ensure_urgency(qs).order_by(
                F('sla_next_due').desc(nulls_last=True)
                if sort_dir == 'desc'
                else F('sla_next_due').asc(nulls_last=True)
            )
        else:
            qs = qs.order_by(f'-{db_sort}' if sort_dir == 'desc' else db_sort)
    elif view != 'trash':
        qs = qs.order_by('-created_at', '-id')

    qs = qs.select_related('brand', 'requester')
    brand = Brand.objects.filter(id=brand_id).only('name').first() if brand_id else None
    view_label = _TICKET_VIEW_LABELS.get(view, 'Todos los tickets')
    brand_label = brand.name if brand else ('Todas las empresas' if not brand_id else f'Empresa #{brand_id}')
    total = qs.count()
    generated_at = timezone.localtime()

    view_filename_label = view_label
    if view_filename_label.lower().startswith('tickets '):
        view_filename_label = view_filename_label[8:]
    brand_token = slugify(brand.name).replace('-', '_') if brand else ''
    view_token = slugify(view_filename_label).replace('-', '_') or 'todos'
    name_parts = ['Tickets']
    if brand_token and brand_token not in view_token:
        name_parts.append(brand_token)
    name_parts.append(view_token)
    dated = _view_uses_date_range(view, request.GET)
    suffix = f'_{date_from:%Y%m%d}-{date_to:%Y%m%d}' if dated else '_sin_limite_de_fecha'
    filename_base = '_'.join(name_parts) + suffix

    metadata = {
        'title': view_label,
        'brand': brand_label,
        'view': view_label,
        'date_from': date_from.strftime('%d/%m/%Y') if dated else 'Sin límite',
        'date_to': date_to.strftime('%d/%m/%Y') if dated else 'Sin límite',
        'generated_at': generated_at.strftime('%d/%m/%Y %H:%M'),
        'total': total,
        'filename_base': filename_base,
    }
    return qs, metadata


def _ticket_export_values(ticket):
    solved_at = ticket.resolved_at or ticket.closed_at
    return [
        ticket.id,
        ticket.get_status_display() or ticket.status or '',
        ticket.service or '',
        ticket.subject,
        ticket.get_type_display() if ticket.type else '',
        ticket.get_channel_display() if ticket.channel else '',
        ticket.requester.name if ticket.requester else '',
        ticket.get_priority_display() if ticket.priority else '',
        timezone.localtime(ticket.created_at).strftime('%Y-%m-%d %H:%M') if ticket.created_at else '',
        timezone.localtime(solved_at).strftime('%Y-%m-%d %H:%M') if solved_at else '',
        ticket.category or '',
    ]


@api_login_required
def tickets_export_csv(request):
    """Exporta exactamente los tickets visibles tras aplicar empresa, fechas y vista."""
    import csv
    from django.http import StreamingHttpResponse

    if request.GET.get('view') == 'trash' and not _can_view_all(request.user):
        return json_error('forbidden', 'Solo agentes pueden ver la papelera.', status=403)
    qs, metadata = _ticket_export_queryset(request)

    class _Echo:
        def write(self, value):
            return value

    writer = csv.writer(_Echo())

    def _rows():
        yield '\ufeff'
        yield writer.writerow(['EXPORTACIÓN DE TICKETS'])
        yield writer.writerow(['Empresa', metadata['brand']])
        yield writer.writerow(['Vista', metadata['view']])
        yield writer.writerow(['Desde', metadata['date_from']])
        yield writer.writerow(['Hasta', metadata['date_to']])
        yield writer.writerow(['Generado', metadata['generated_at']])
        yield writer.writerow(['Tickets resultantes', metadata['total']])
        yield writer.writerow([])
        yield writer.writerow([
            'ID', 'Status', 'Service', 'Subject', 'Type', 'Channel',
            'Requester', 'Priority', 'Requested', 'Solved', 'Category',
        ])
        for ticket in qs.iterator(chunk_size=2000):
            yield writer.writerow(_ticket_export_values(ticket))

    response = StreamingHttpResponse(_rows(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="{metadata["filename_base"]}.csv"'
    )
    return response


@api_login_required
def tickets_export_pdf(request):
    """Exporta el mismo conjunto del CSV como fichas de tickets paginadas."""
    from .services.ticket_exports import build_tickets_pdf

    if request.GET.get('view') == 'trash' and not _can_view_all(request.user):
        return json_error('forbidden', 'Solo agentes pueden ver la papelera.', status=403)
    qs, metadata = _ticket_export_queryset(request)
    pdf_bytes = build_tickets_pdf(qs.iterator(chunk_size=500), metadata)
    response = HttpResponse(pdf_bytes, content_type='application/pdf')
    response['Content-Disposition'] = (
        f'attachment; filename="{metadata["filename_base"]}.pdf"'
    )
    return response

@api_login_required
def filter_customers(request):
    if not _can_view_all(request.user) and not _is_admin(request.user):
         return JsonResponse({"error": "No permission"}, status=403)

    view = request.GET.get("view")
    sort_by  = request.GET.get("sort_by", "")
    sort_dir = request.GET.get("sort_dir", "asc") if request.GET.get("sort_dir") in ("asc", "desc") else "asc"
    q = (request.GET.get("q") or "").strip()
    role_id = (request.GET.get("role_id") or "").strip()
    group_id = (request.GET.get("group_id") or "").strip()
    try:
        page = max(1, int(request.GET.get("page", 1) or 1))
    except (ValueError, TypeError):
        page = 1
    page_size = _parse_page_size(request.GET.get("page_size"))

    # 'id' como desempate: sin una clave única el ORDER BY no es determinista y
    # LIMIT/OFFSET puede repetir u omitir filas al cambiar de página (hay nombres
    # duplicados de sobra en la tabla de usuarios).
    base_queryset = User.objects.select_related('role', 'group').order_by('name', 'id')
    if not _is_admin(request.user):
         base_queryset = base_queryset.exclude(role__role_name__in=['admin', 'administrator'])

    if view == "suspended":
        users = base_queryset.filter(group__group_name="Suspended")
    else:  # "all"
        users = base_queryset.exclude(group__group_name="Suspended")

    if q:
        users = users.filter(Q(name__icontains=q) | Q(email__icontains=q))
    if role_id.isdigit():
        users = users.filter(role_id=int(role_id))
    if group_id.isdigit():
        users = users.filter(group_id=int(group_id))

    filtros = {
        "all": base_queryset.exclude(group__group_name="Suspended").count(),
        "suspended": base_queryset.filter(group__group_name="Suspended").count(),
    }

    db_sort = _CUSTOMER_SORT_FIELDS.get(sort_by)
    if db_sort:
        users = users.order_by(f'-{db_sort}' if sort_dir == 'desc' else db_sort, 'id')

    total = users.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    offset = (page - 1) * page_size

    page_qs = users[offset:offset + page_size]

    data = [
        {
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "status": "Suspendido" if (u.group and u.group.group_name == "Suspended") else "Activo",
            "created_at": timezone.localtime(u.created_at).strftime("%d/%m/%Y %H:%M"),
            "role": u.role.role_name if u.role else "-",
            "group": u.group.group_name if u.group else "-",
        }
        for u in page_qs
    ]

    return JsonResponse({
        "customers": data,
        "filtros": filtros,
        "pagination": {
            "total": total,
            "page": page,
            "total_pages": total_pages,
            "page_size": page_size,
        },
        "sort": {"sort_by": sort_by if db_sort else "", "sort_dir": sort_dir},
    })

@login_required
def customer_profile(request):
    if str(request.GET.get('id', '')) == str(request.user.pk):
        from django.urls import reverse
        target = reverse('profile')
        if request.GET.get('fragment') == '1':
            target += '?fragment=1'
        return redirect(target)
    if not _can_view_all(request.user) and not _is_admin(request.user):
        return render(request, "tickets/403.html", {"error": "No tienes permisos para ver este perfil."}, status=403)
    customer_id = request.GET.get("id")
    customer = get_object_or_404(User, id=customer_id)

    base_qs = Ticket.objects.filter(
        Q(requester__id=customer.id) | Q(assignee__id=customer.id) |
        Q(ccs__id=customer.id) | Q(created_by_id=customer.id)
    ).distinct()
    ctx = _profile_tickets_context(request, base_qs.filter(is_deleted=False))
    ctx["customer"] = customer
    return render(request, "tickets/customer_profile.html", ctx)

@api_login_required
def tags_api(request):
    q = request.GET.get('q', '')
    tags = TicketTag.objects.filter(name__icontains=q) if q else TicketTag.objects.all()
    results = [{"id": tag.id, "text": tag.name} for tag in tags]
    return JsonResponse({"results": results})

@login_required
def customers_list(request):
    if not _can_view_all(request.user) and not _is_admin(request.user):
         return render(request, "tickets/403.html", {"error": "No tienes permisos para ver clientes."}, status=403)

    role_qs = Role.objects.all().order_by('role_name')
    group_qs = Group.objects.all().order_by('group_name')
    if not _is_admin(request.user):
        role_qs = role_qs.exclude(role_name__in=['admin', 'administrator'])

    active_view = request.GET.get('view')
    if active_view not in {'all', 'suspended'}:
        active_view = 'all'
    view_label = 'Usuarios suspendidos' if active_view == 'suspended' else 'Todos los clientes'

    context = {
        'username': request.user.name,
        'email': request.user.email,
        'is_admin': _is_admin(request.user),
        'roles': role_qs,
        'groups': group_qs,
        'active_customer_view': active_view,
        'customer_view_label': view_label,
    }
    return render(request, "tickets/customers_list.html", context)

@login_required
def reporting(request):
    if not _can_view_all(request.user) and not _is_admin(request.user):
        return render(request, "tickets/403.html", {"error": "No tienes permisos para ver los reportes."}, status=403)
    agents = [
        user for user in User.objects.filter(is_active=True).select_related('role').order_by('name')
        if permission_is_agent(user)
    ]
    context = {
        'username': request.user.name,
        'email': request.user.email,
        'product_lines': list(ProductLine.objects.filter(active=True).order_by('sort_order', 'name').values('id', 'code', 'name', 'color', 'icon')),
        'groups': list(Group.objects.order_by('group_name').values('id', 'group_name')),
        'agents': agents,
        'organizations': list(Organization.objects.order_by('name').values('id', 'name')),
        'channels': CHANNEL_CHOICES,
        'priorities': Ticket._meta.get_field('priority').choices,
        'ticket_types': TICKET_TYPE_CHOICES,
    }
    return render(request, "tickets/reporting.html", context)


def _reporting_params(request):
    """Parsea brand/from/to con defaults (últimos 30 días) y clamp a 366 días."""
    brand_id = _as_int_or_none(request.GET.get('brand'))
    date_from, date_to, start_dt, end_dt = _parse_filter_date_range(request.GET)
    return brand_id, date_from, date_to, start_dt, end_dt


def _reporting_cache_get(key):
    """La caché acelera el informe, pero nunca debe decidir su disponibilidad."""
    try:
        return cache.get(key)
    except Exception:
        logger.exception('reporting_cache_get_failed')
        return None


def _reporting_cache_set(key, payload, timeout=60):
    try:
        cache.set(key, payload, timeout)
    except Exception:
        logger.exception('reporting_cache_set_failed')


@api_login_required
def reporting_data(request):
    """KPIs, series y distribuciones por entidad para /reporting/."""
    if not _can_view_all(request.user) and not _is_admin(request.user):
        return json_error('forbidden', 'No tienes permisos para ver los reportes.', status=403)

    section = (request.GET.get('section') or '').strip()
    if section:
        from .services.reporting_metrics import ReportFilters, SECTION_BUILDERS, build_reporting_section

        if section not in SECTION_BUILDERS:
            return json_error('invalid_section', 'La zona de reporting no existe.', status=400)
        brand_id, date_from, date_to, start_dt, end_dt = _reporting_params(request)
        filters = ReportFilters.from_params(request.GET)
        cache_key = (
            f"reporting:product:v3:{section}:{date_from}:{date_to}:"
            f"{filters.cache_fragment()}"
        )
        cached = _reporting_cache_get(cache_key)
        if cached is not None:
            return JsonResponse(cached)
        try:
            payload = build_reporting_section(
                section, filters, date_from, date_to, start_dt, end_dt
            )
        except Exception:
            logger.exception('reporting_section_failed', extra={'section': section})
            return json_error(
                'reporting_failed',
                'No se han podido calcular los datos de esta zona.',
                status=500,
            )
        _reporting_cache_set(cache_key, payload, 60)
        return JsonResponse(payload)

    brand_id, date_from, date_to, start_dt, end_dt = _reporting_params(request)
    cache_key = f"reporting:v2:{brand_id or 0}:{date_from}:{date_to}"
    cached = _reporting_cache_get(cache_key)
    if cached is not None:
        return JsonResponse(cached)

    from .services.reporting_metrics import build_legacy_overview

    payload = build_legacy_overview(brand_id, date_from, date_to, start_dt, end_dt)
    _reporting_cache_set(cache_key, payload, 60)
    return JsonResponse(payload)


@api_login_required
def reporting_export_csv(request):
    """Exporta a CSV (compatible Excel) el conjunto filtrado del reporting."""
    import csv
    from django.http import StreamingHttpResponse

    if not _can_view_all(request.user) and not _is_admin(request.user):
        return json_error('forbidden', 'No tienes permisos para exportar.', status=403)

    from .services.reporting_metrics import ReportFilters

    _brand_id, date_from, date_to, start_dt, end_dt = _reporting_params(request)
    filters = ReportFilters.from_params(request.GET)
    qs = filters.apply(Ticket.objects.filter(
        is_deleted=False, merged_into__isnull=True,
        created_at__range=(start_dt, end_dt),
    ))
    _last_rating = SatisfactionRating.objects.filter(
        ticket=OuterRef('pk'), score__in=['good', 'bad']
    ).order_by('-created_at')
    rating_sub = _last_rating.values('score')[:1]
    # Motivo del voto: del catálogo si es nativo, del texto libre si viene de Zendesk.
    reason_sub = _last_rating.values('reason_choice__label')[:1]
    reason_text_sub = _last_rating.values('reason')[:1]
    qs = (
        qs.select_related('product_line', 'requester', 'assignee')
        .annotate(
            satisfaction=Subquery(rating_sub),
            satisfaction_reason=Subquery(reason_sub),
            satisfaction_reason_text=Subquery(reason_text_sub),
        )
        .order_by('id')
    )

    class _Echo:
        def write(self, value):
            return value

    writer = csv.writer(_Echo(), delimiter=';')

    def _rows():
        # BOM para que Excel abra acentos en UTF-8 correctamente
        yield '﻿'
        yield writer.writerow([
            'id', 'asunto', 'producto_servicio', 'estado', 'prioridad', 'tipo', 'canal',
            'solicitante', 'agente', 'creado', 'cerrado', 'primera_respuesta_ok',
            'sla_incumplido', 'satisfaccion', 'motivo_satisfaccion',
        ])
        for t in qs.iterator(chunk_size=2000):
            yield writer.writerow([
                t.id,
                t.subject,
                t.product_line.name if t.product_line else 'Sin clasificar',
                t.status,
                t.priority or '',
                t.type or '',
                t.channel or '',
                t.requester.name if t.requester else '',
                t.assignee.name if t.assignee else '',
                timezone.localtime(t.created_at).strftime('%d/%m/%Y %H:%M') if t.created_at else '',
                timezone.localtime(t.closed_at).strftime('%d/%m/%Y %H:%M') if t.closed_at else '',
                {True: 'si', False: 'no'}.get(t.first_response_met, ''),
                'si' if t.sla_breached_at else 'no',
                t.satisfaction or '',
                t.satisfaction_reason or (t.satisfaction_reason_text or ''),
            ])

    product_part = f'producto{filters.product_line_id}' if filters.product_line_id else 'todos-productos'
    response = StreamingHttpResponse(_rows(), content_type='text/csv; charset=utf-8')
    response['Content-Disposition'] = (
        f'attachment; filename="tickets_{product_part}_{date_from}_{date_to}.csv"'
    )
    return response

@login_required
def profile(request):
    user = request.user
    base_qs = Ticket.objects.filter(
        Q(requester__id=user.id) | Q(assignee__id=user.id) |
        Q(ccs__id=user.id) | Q(created_by_id=user.id)
    ).distinct()
    ctx = _profile_tickets_context(request, base_qs.filter(is_deleted=False))
    ctx["user"] = user
    mine = _my_tickets_queryset(_base_tickets_queryset(user), user)
    ctx['workspace_activity'] = {
        'open': mine.filter(status='open').count(),
        'pending': mine.filter(status='pending').count(),
        'on_hold': mine.filter(status='on_hold').count(),
        'resolved': mine.filter(status__in=('resolved', 'closed'),
                                updated_at__gte=timezone.now() - timedelta(days=60)).count(),
    }
    return render(request, "tickets/profile.html", ctx)


@api_login_required
@require_POST
def save_workspace_profile(request):
    from django.core.validators import URLValidator
    from django.core.exceptions import ValidationError
    try:
        data = (json.loads(request.POST.get('payload', '{}')) if request.content_type == 'multipart/form-data'
                else parse_json_body(request))
    except (APIValidationError, ValueError):
        return json_error('invalid_json', 'Los datos del perfil no son válidos.', status=400)
    if not isinstance(data, dict):
        return json_error('invalid_profile', 'Los datos del perfil no son válidos.', status=400)
    name = data.get('name', request.user.name)
    phone = data.get('phone', request.user.phone or '')
    photo = data.get('photo_url', request.user.photo_url or '')
    prefs = data.get('preferences', {})
    if (not isinstance(name, str) or not name.strip() or len(name) > 255
            or not isinstance(phone, str) or len(phone) > 64
            or not isinstance(photo, str) or len(photo) > 500
            or not isinstance(prefs, dict)):
        return json_error('invalid_profile', 'Revisa el nombre, teléfono y foto.', status=400)
    if photo and 'photo_url' in data:
        try:
            URLValidator(schemes=['https'])(photo)
        except ValidationError:
            return json_error('invalid_photo', 'La foto debe ser una dirección HTTPS válida.', status=400)
    if (prefs.get('theme', 'system') not in ('system', 'light', 'dark')
            or prefs.get('density', 'comfortable') not in ('comfortable', 'compact')
            or prefs.get('text_size', 'normal') not in ('normal', 'large')):
        return json_error('invalid_preferences', 'Revisa las preferencias de apariencia.', status=400)
    signature = prefs.get('signature', '')
    if not isinstance(signature, str) or len(signature) > 2000:
        return json_error('invalid_preferences', 'La firma admite hasta 2000 caracteres.', status=400)
    for key in ('notification_sound', 'notification_animation'):
        if key in prefs and type(prefs[key]) is not bool:
            return json_error('invalid_preferences', 'Las preferencias de notificaciones no son válidas.', status=400)
    clean = {'theme': prefs.get('theme', 'system'), 'density': prefs.get('density', 'comfortable'),
             'text_size': prefs.get('text_size', 'normal'), 'signature': signature.strip(),
             'notification_sound': prefs.get('notification_sound', False),
             'notification_animation': prefs.get('notification_animation', True)}
    user = request.user
    upload = request.FILES.get('photo')
    if upload:
        import io
        import uuid
        from PIL import Image, ImageOps, UnidentifiedImageError
        from django.core.files.base import ContentFile
        from django.core.files.storage import default_storage
        if upload.size > 5 * 1024 * 1024:
            return json_error('invalid_photo', 'La imagen debe ocupar como máximo 5 MB.', status=400)
        try:
            image = Image.open(upload)
            if image.format not in ('JPEG', 'PNG', 'WEBP') or image.width * image.height > 20_000_000:
                raise ValueError('Formato o dimensiones no admitidos')
            image.load()
            image = ImageOps.fit(ImageOps.exif_transpose(image).convert('RGB'), (256, 256))
            output = io.BytesIO()
            image.save(output, format='JPEG', quality=88)
        except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
            return json_error('invalid_photo', 'Selecciona una imagen JPG, PNG o WebP válida.', status=400)
        path = default_storage.save(f'profiles/{user.pk}/{uuid.uuid4().hex}.jpg', ContentFile(output.getvalue()))
        photo = default_storage.url(path)
    user.name, user.phone, user.photo_url, user.workspace_preferences = name.strip(), phone.strip(), photo, clean
    user.save(update_fields=['name', 'phone', 'photo_url', 'workspace_preferences', 'updated_at'])
    return JsonResponse({'ok': True, 'preferences': clean, 'name': user.name, 'photo_url': photo})

@ensure_csrf_cookie
def login_redirect(request):
    """Redirige al login corporativo SSO."""
    sso_url = getattr(DJANGO_SETTINGS, 'SSO_LOGIN_UI_URL', 'https://login.recordia.net/')
    return redirect(sso_url)


def dev_login(request):
    """Login simplificado sin SSO. Solo disponible cuando DEBUG=True.
    Permite trabajar en local sin pasar por dev-login.recordia.net."""
    from django.http import Http404
    if not DJANGO_SETTINGS.DEBUG:
        raise Http404()

    error = None
    if request.method == 'POST':
        email = (request.POST.get('email') or '').strip()
        try:
            user = User.objects.get(email=email)
            user.backend = 'app.backends.EmailBackend'
            auth_login(request, user)
            return redirect('home')
        except User.DoesNotExist:
            error = f"No existe usuario con email '{email}' en la base de datos local."

    users = User.objects.order_by('email').values_list('email', flat=True)[:50]
    return render(request, 'tickets/dev_login.html', {
        'error': error,
        'users': list(users),
    })

@ensure_csrf_cookie
def sso_callback(request):
    """Vista que carga el frontend para procesar el token SSO."""
    return render(request, "tickets/sso_callback.html", {
        "SSO_LOGIN_API_URL": getattr(DJANGO_SETTINGS, 'SSO_LOGIN_API_URL', 'https://login-api.agentia365.com')
    })

@require_POST
def sso_complete(request):
    """
    Recibe la identidad extraída por el frontend desde Authentication/Me.
    Verifica o crea el usuario local y genera la sesión de Django.
    """
    try:
        data = parse_json_body(request)
        email = data.get('email')
        name = data.get('name', 'Usuario SSO')

        if not email:
            return json_error('email_required', 'Email es requerido para completar el SSO', status=400)

        # Buscar o crear el usuario localmente
        user, created = User.objects.get_or_create(email=email)
        if created or not user.name or user.name == 'Usuario SSO':
            user.name = name
            if created:
                agent_role, _ = Role.objects.get_or_create(role_name='agent')
                user.role = agent_role
            user.save()

        # Iniciar sesión local en Django para emitir la session cookie de Django 
        # (independiente de la RecordiaAuthToken que usa la API)
        user.backend = 'app.backends.EmailBackend' 
        auth_login(request, user)

        return JsonResponse({'ok': True, 'data': {'status': 'ok'}, 'status': 'ok', 'message': 'Sesión completada exitosamente'})
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)
    except Exception as e:
        logger.error(f"Error en sso_complete: {str(e)}")
        return json_error('sso_complete_failed', str(e), status=500)

def register(request):
    if request.method == 'POST':
        try:
            data = parse_json_body(request)
        except APIValidationError as exc:
            return json_error(exc.code, exc.message, status=exc.status)
        email = data.get('email')
        name = data.get('name')
        password = data.get('password')
        if not email or not name or not password:
            return json_error('missing_fields', 'Email, nombre y password son obligatorios', status=400)
        if User.objects.filter(email=email).exists():
            return json_error('user_exists', 'El usuario ya existe', status=400)
        user = User(email=email, name=name)
        user.set_password(password)  # Hashear la contrase�a
        user.save()
        return json_ok(message='Usuario registrado correctamente')

def user_login(request):
    if request.method == 'POST':
        try:
            data = parse_json_body(request)
            email = data.get('email')
            password = data.get('password')

            # Autenticar al usuario con el backend personalizado
            user = authenticate(request, username=email, password=password)

            if user is not None:
                auth_login(request, user, backend='app.backends.EmailBackend')  # Forzar el backend correcto
                return json_ok(message='Inicio de sesión exitoso')
            else:
                return json_error('invalid_credentials', 'Credenciales inválidas', status=400)
        except APIValidationError as exc:
            return json_error(exc.code, exc.message, status=exc.status)
        except Exception as e:
            return json_error('login_failed', str(e), status=500)
    return json_error('method_not_allowed', 'Method not allowed', status=405)

def user_logout(request):
    logout(request)  # Cierra la sesión del usuario
    return redirect('login') # Redirige al login
   
def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    return render(request, 'tickets/ticket_detail.html', {'ticket': ticket})


@api_login_required
def ticket_detail_api(request, ticket_id):
    ticket = get_object_or_404(
        Ticket.objects.select_related('brand', 'product_line', 'requester', 'assignee'),
        id=ticket_id,
    )
    if not can_view_ticket(request.user, ticket):
        return json_error("forbidden", "No tienes acceso a este ticket.", status=403)

    # Marcar como leídas las notificaciones del usuario para este ticket
    Notification.objects.filter(user=request.user, ticket=ticket, read=False).update(read=True)

    data = {
        'empresa': ticket.brand.name if ticket.brand else '',
        'brand_id': ticket.brand_id,
        'producto': ticket.product_line_id,
        'producto_name': ticket.product_line.name if ticket.product_line else '',
        'solicitante': ticket.requester_id,
        'solicitante_name': ticket.requester.name if ticket.requester else '',
        'asignado': ticket.assignee_id,
        'asignado_name': ticket.assignee.name if ticket.assignee else '',
        'grupo': ticket.assigned_group_id,
        # ccs: list of {id, name} objects so the frontend can pre-populate AJAX Select2
        'ccs': [{'id': u.id, 'name': u.name} for u in ticket.ccs.all()],
        'tags': [tag.id for tag in ticket.tags.all()],
        'tipo': ticket.type,
        'problem_id': ticket.problem_id,
        'problem_subject': ticket.problem.subject if ticket.problem_id and ticket.problem else None,
        'problem_status': ticket.problem.status if ticket.problem_id and ticket.problem else None,
        'incidents': [
            {'id': inc.id, 'subject': inc.subject, 'status': inc.status}
            for inc in ticket.incidents.filter(is_deleted=False)[:50]
        ] if ticket.type == 'problem' else [],
        'prioridad': ticket.priority,
        'servicio': ticket.service,
        'canal': ticket.channel,
        'idioma': ticket.language,
        'categoria': ticket.category,
        'security_related': ticket.security_related,
        'monitoring': ticket.monitoring,
        'approval_status': ticket.approval_status,
        'resolution_type': ticket.resolution_type,
        'required_tasks': ticket.required_tasks,
        'first_response_due_at': ticket.first_response_due_at.isoformat() if ticket.first_response_due_at else None,
        'resolution_due_at': ticket.resolution_due_at.isoformat() if ticket.resolution_due_at else None,
        'sla_breached_at': ticket.sla_breached_at.isoformat() if ticket.sla_breached_at else None,
        'subject': ticket.subject,
        'content': ticket.description,
    }
    return JsonResponse(data)


@api_login_required
@require_GET
def users_search_api(request):
    """
    AJAX endpoint for Select2 user search.
    Returns paginated {results: [{id, text}], pagination: {more}} compatible with Select2.
    """
    q = (request.GET.get('q') or '').strip()
    try:
        page = max(1, int(request.GET.get('page') or 1))
    except (ValueError, TypeError):
        page = 1
    page_size = 30

    qs = User.objects.filter(is_active=True).only('id', 'name', 'email').order_by('name')
    if not _can_view_all(request.user) and not _is_admin(request.user):
        qs = qs.filter(id=request.user.id)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q))

    offset = (page - 1) * page_size
    users = list(qs[offset:offset + page_size])
    has_more = len(users) == page_size  # if full page, there might be more

    return JsonResponse({
        # El email va aparte para que el desplegable lo pinte como segunda
        # línea: con solo el nombre no se distinguen homónimos ni los nombres
        # sacados del propio email.
        'results': [{'id': u.id, 'text': u.name, 'email': u.email} for u in users],
        'pagination': {'more': has_more},
    })


@api_login_required
@require_POST
def quick_create_end_user(request):
    """Alta de solicitante desde el desplegable «Solicitante» del ticket."""
    if not _is_agent(request.user):
        return json_error('forbidden', 'Permiso denegado', status=403)
    try:
        data = parse_json_body(request)
        user, created = UserAdminService.create_end_user(data.get('email'), data.get('name'))
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)
    return JsonResponse({
        'ok': True,
        'created': created,
        'user': {'id': user.id, 'text': user.name, 'email': user.email},
    })


def _is_agent(user):
    return permission_is_agent(user)

def _is_admin(user):
    return permission_is_admin(user)


def _can_view_all(user):
    """Lectura de todo: agentes y el rol de solo lectura (#21378). Escribir sigue
    exigiendo _is_agent."""
    return can_view_all_tickets(user)


@login_required
def legacy_settings_redirect(request):
    """Keep old bookmarks useful without restoring the removed settings screen."""
    destination = 'admin_panel' if _is_admin(request.user) else 'profile'
    return redirect(destination)


@login_required
def create_ticket(request):
    id_param = request.GET.get('id', None)

    if request.method == 'POST':
        try:
            ticket_id = int(id_param) if id_param and id_param.isdigit() else None
            ticket = TicketService.create_or_update_from_post(request.user, request.POST, ticket_id=ticket_id)
        except APIValidationError as exc:
            return json_error(exc.code, exc.message, status=exc.status)
        except Ticket.DoesNotExist:
            return json_error('ticket_not_found', 'Ticket no encontrado', status=404)
        url = f'{request.path}?id={ticket.id}'
        if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
            return JsonResponse({
                'ticket_id': ticket.id, 'url': url,
                'ticket': _ticket_header_payload(ticket),
            })
        return redirect(url)

    # GET .
    if not (id_param and id_param.isdigit()) and is_readonly(request.user):
        return render(request, "tickets/403.html",
                      {"error": "Tu rol es de solo lectura: no puedes crear tickets."}, status=403)
    # Gate: end users only see tickets they are involved in (requester / cc / created_by).
    # Otherwise their tab opens and renders, but the JS XHR /api/tickets/<id>/ fails with
    # 403 and form fields stay empty — which the user perceives as "ticket viewing error".
    if id_param and id_param.isdigit():
        _ticket_check = get_object_or_404(Ticket, id=int(id_param))
        if not can_view_ticket(request.user, _ticket_check):
            return render(request, "tickets/403.html",
                          {"error": "No tienes acceso a este ticket."}, status=403)

    usuarios = _cached_all_users()
    agentes = AssignmentService.agent_queryset().only('id', 'name', 'email').order_by('name')
    tags = TicketTag.objects.only('id', 'name').all()
    todos = usuarios  # same list — avoids a duplicate 663ms query
    grupos = Group.objects.all().order_by('group_name')

    empresas = list(Brand.objects.order_by('name').values_list('name', flat=True))
    product_lines = list(ProductLine.objects.filter(active=True).order_by('sort_order', 'name'))

    ticket_obj = get_object_or_404(Ticket, id=int(id_param)) if id_param and id_param.isdigit() else None

    # --- Historial (interacciones) del solicitante: sus tickets más recientes ---
    user_timeline = []
    if ticket_obj and ticket_obj.requester_id:
        # Subquery para obtener el último comentario público en una sola consulta (evita N+1)
        last_comment_sub = Comment.objects.filter(
            ticket=OuterRef('pk'),
            is_public=True,
        ).order_by('-created_at').values('content')[:1]

        related = (Ticket.objects
                   .filter(requester_id=ticket_obj.requester_id, is_deleted=False)
                   .select_related('requester', 'assignee')
                   .annotate(last_pub_content=Subquery(last_comment_sub))
                   .order_by('-updated_at')[:30])

        for t in related:
            raw = t.last_pub_content or ''
            snippet = (raw[:160] + '…') if len(raw) > 160 else raw

            desc_raw = t.description or ''
            desc_snippet = (desc_raw[:200] + '…') if len(desc_raw) > 200 else desc_raw

            user_timeline.append({
                'id': t.id,
                'zendesk_id': t.zendesk_id,
                'subject': t.subject,
                'status': t.status,
                'updated_at': timezone.localtime(t.updated_at).strftime('%d/%m/%Y %H:%M'),
                'description': desc_snippet,
                'last_comment': snippet,
            })
    # Build combined feed: comments + audit events, sorted by created_at
    _FIELD_LABEL = {
        'status': 'estado', 'assignee_id': 'asignado', 'group_id': 'grupo',
        'priority': 'prioridad', 'tags': 'tags', 'subject': 'asunto',
        'requester_id': 'solicitante', 'product_line_id': 'producto / servicio',
        'deleted': 'eliminación', 'restored': 'restauración',
    }
    if ticket_obj:
        if _can_view_all(request.user):
            comments_list = list(
                ticket_obj.comment_set.select_related('user', 'user__role').prefetch_related('attachment_set').order_by('created_at')
            )
        else:
            comments_list = list(
                ticket_obj.comment_set.filter(is_public=True)
                .select_related('user', 'user__role').prefetch_related('attachment_set').order_by('created_at')
            )
        events_list = list(ticket_obj.events.select_related('actor').order_by('created_at'))
        # Resolver también los eventos antiguos sin alterar los IDs de auditoría.
        assignee_fields = {'assignee_id', 'assignee', 'asignado'}
        assignee_ids = {
            int(value) for event in events_list if event.field_name in assignee_fields
            for value in (event.old_value, event.new_value)
            if value and value.isdecimal() and len(value) <= 18
        }
        assignee_names = {
            str(user.pk): user.name or user.email
            for user in User.objects.filter(pk__in=assignee_ids)
        }

        def event_value(event, value):
            if event.field_name not in assignee_fields:
                return value
            if not value or value in ('None', 'null'):
                return 'Sin asignar'
            if value.isdecimal():
                return assignee_names.get(str(int(value)), 'Agente no disponible')
            return value

        feed = []
        for c in prepare_comments(comments_list):
            c.attachment_cards = [AttachmentService.metadata(a) for a in c.attachment_set.all()
                                  if a.ticket_id == ticket_obj.id and not a.is_inline]
            feed.append({'item_type': 'comment', 'obj': c})
        for e in events_list:
            feed.append({'item_type': 'event', 'obj': e,
                         'old_display': event_value(e, e.old_value),
                         'new_display': event_value(e, e.new_value),
                         'field_label': _FIELD_LABEL.get(e.field_name, e.field_name)})
        feed.sort(key=lambda x: x['obj'].created_at)
    else:
        feed = []

    ticket_satisfaction = None
    if ticket_obj:
        ticket_satisfaction = (
            SatisfactionRating.objects
            .filter(ticket=ticket_obj, score__in=['good', 'bad'])
            .order_by('-created_at')
            .first()
        )

    context = {
        'usuarios': usuarios,
        'agentes': agentes,
        'tags': tags,
        'todos': todos,
        'empresas': empresas,
        'product_lines': product_lines,
        'grupos': grupos,
        'username': request.user.name,
        'email': request.user.email,
        'ticket': ticket_obj,
        'ticket_locked': bool(ticket_obj and (ticket_obj.is_deleted or ticket_obj.status == 'closed')),
        'feed': feed,
        'can_use_internal': _can_view_all(request.user),
        'can_edit': _is_agent(request.user),
        'user_timeline': user_timeline,
        'ticket_satisfaction': ticket_satisfaction,
        'current_user_id': request.user.id,
        'attachment_limits': AttachmentService.limits(),
    }
    # Fragment mode: ?fragment=1 returns only the ticket pane HTML (no base template).
    # Used by tabs.js to swap panes in-place without full page reload.
    if request.GET.get('fragment') == '1':
        return render(request, 'tickets/_ticket_pane.html', context)
    return render(request, 'tickets/create_ticket.html', context)

class TagListAPIView(View):
    def get(self, request):
        query = request.GET.get('q', '')
        tags = TicketTag.objects.filter(name__icontains=query)[:10]
        results = [{'id': tag.id, 'text': tag.name} for tag in tags]
        return JsonResponse({'results': results})

@login_required
def assign_agent(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if request.method == 'POST':
        agent_id = request.POST.get('agent_id')
        ticket.assigned_to_id = agent_id
        ticket.save()
        return redirect('ticket_detail', pk=pk)
    agents = User.objects.filter(role__role_name='agent')
    return render(request, 'tickets/assign_agent.html', {'ticket': ticket, 'agents': agents})

@login_required
def update_ticket(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    if request.method == 'POST':
        form = TicketForm(request.POST, instance=ticket)
        if form.is_valid():
            form.save()
            return redirect('ticket_detail', pk=ticket.pk)
    else:
        form = TicketForm(instance=ticket)
    return render(request, 'tickets/update_ticket.html', {'form': form, 'ticket': ticket})

@login_required
def close_ticket(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    ticket.status = 'closed'
    ticket.closed_at = timezone.now()
    ticket.save()
    return redirect('ticket_detail', pk=pk)

@login_required
def reopen_ticket(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    ticket.status = 'open'
    ticket.save()
    return redirect('ticket_detail', pk=pk)

@api_login_required
@require_POST
def take_ticket(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    if not can_update_ticket(request.user, ticket):
        return json_error('forbidden', 'Permiso denegado', status=403, success=False)
    with transaction.atomic():
        ticket = Ticket.objects.select_for_update().get(id=ticket_id)
        if ticket.is_deleted or ticket.status == 'closed':
            return json_error('ticket_locked', 'El ticket está eliminado o cerrado.', status=409)
        if ticket.assignee_id == request.user.id:
            return JsonResponse({'ok': False, 'success': False, 'error': 'already_assigned'})
        prev = str(ticket.assignee_id) if ticket.assignee_id else None
        ticket.assignee = request.user
        ticket.save(update_fields=['assignee', 'updated_at'])
        TicketEvent.objects.create(
            ticket=ticket, actor=request.user,
            field_name='assignee_id',
            old_value=prev,
            new_value=str(request.user.id),
            created_at=timezone.now(),
        )
        NotificationService.notify_ticket_users(ticket, f"Ticket #{ticket.id} asignado a {request.user.name}", actor=request.user)
    return JsonResponse({'ok': True, 'success': True, 'data': {'assignee_id': request.user.id, 'assignee_name': request.user.name}, 'assignee_id': request.user.id, 'assignee_name': request.user.name})


# Funcionalidades de Comentarios

def _ticket_header_payload(ticket):
    """Cabecera del pane: estado, asignado, prioridad y fecha.

    La devuelven las tres rutas que pueden cambiarla —publicar, guardar campos
    y el sondeo—, para que todas repinten lo mismo. Sin esto, publicar dejaba
    la chapa de estado con el valor con el que se cargo la pagina.
    """
    return {
        'status': ticket.status,
        'status_display': ticket.get_status_display(),
        'assignee_id': ticket.assignee_id,
        'assignee_name': ticket.assignee.name if ticket.assignee else '',
        'priority': ticket.priority or '',
        'updated_at': (
            timezone.localtime(ticket.updated_at).strftime('%d/%m/%Y %H:%M')
            if ticket.updated_at else ''
        ),
        # Reloj del servidor para ordenar respuestas: el sondeo y la publicacion
        # compiten, y sin esto la del sondeo —leida antes de publicar— repinta
        # la cabecera con el estado antiguo.
        'updated_ts': ticket.updated_at.timestamp() if ticket.updated_at else 0,
    }


def _comment_payload(comment, ticket):
    """Forma canonica de un comentario para el frontend.

    La consumen add_comment (publicacion propia) y ticket_live (refresco por
    sondeo), de modo que ambas rutas pintan el mismo DOM y no pueden divergir.
    `comment` tiene que venir de prepare_comments: de ahi salen `conversation`
    y `speaker_kind`. El autor se lee del propio comentario y no del usuario de
    la peticion, porque en el sondeo casi nunca son el mismo.
    """
    from django.template.loader import render_to_string

    author = comment.user
    return {
        'id': comment.id,
        'content': comment.content,
        'html_body': comment.html_body or '',
        'conversation_html': render_to_string(
            'tickets/_conversation_body.html', {'conversation': comment.conversation},
        ),
        'speaker_kind': comment.speaker_kind,
        'created_at': timezone.localtime(comment.created_at).strftime('%Y-%m-%d %H:%M:%S'),
        'created_at_iso': comment.created_at.isoformat(),
        'ticket_id': ticket.id,
        'user_id': author.id if author else None,
        'username': author.name if author else '',
        'user_photo_url': author.photo_url if author else '',
        'user_role': author.role.role_name if author and author.role else None,
        'is_public': comment.is_public,
        'attachments': [
            AttachmentService.metadata(a) for a in comment.attachment_set.order_by('id')
            if a.ticket_id == ticket.id and not a.is_inline
        ],
    }


@api_login_required
@require_POST
def add_comment(request, ticket_id):
    try:
        data = parse_json_body(request)
        if not isinstance(data, dict):
            raise APIValidationError('invalid_json', 'Se requiere un objeto JSON.', status=400)
        ticket = get_object_or_404(Ticket, id=ticket_id)
        # Sin mensaje ni adjuntos pero con estado: es un cambio de estado a secas
        # (poner en pendiente una alarma no exige escribir nada).
        status_only = (
            data.get('new_status')
            and not data.get('attachment_ids')
            and not str(data.get('content') or '').strip()
            and not str(data.get('html_body') or '').strip()
        )
        if status_only:
            ticket = TicketService.change_status(request.user, ticket.id, data.get('new_status'))
            return JsonResponse({
                'status_only': True,
                'new_status': ticket.status,
                'ticket': _ticket_header_payload(ticket),
            })
        comment, applied_status = CommentService.add_comment(
            ticket=ticket,
            actor=request.user,
            content=data.get('content') or '',
            is_public=data.get('is_public', True),
            html_body=data.get('html_body') or '',
            new_status=data.get('new_status') or '',
            attachment_ids=data.get('attachment_ids'),
            request_id=data.get('request_id'),
        )
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)

    visible_comments = ticket.comment_set.select_related('user', 'user__role').order_by('created_at', 'id')
    if not _is_agent(request.user):
        visible_comments = visible_comments.filter(is_public=True)
    presented = prepare_comments(list(visible_comments))
    published = next(c for c in presented if c.pk == comment.pk)
    # add_comment relee el ticket con select_for_update y muta esa copia, asi
    # que esta instancia sigue con el estado con el que entro la peticion.
    ticket.refresh_from_db()
    payload = _comment_payload(published, ticket)
    payload['new_status'] = applied_status
    payload['ticket'] = _ticket_header_payload(ticket)
    return JsonResponse(payload)


@api_login_required
@require_GET
def ticket_live(request, ticket_id):
    """Novedades de un ticket desde el ultimo comentario que vio el cliente.

    La llama el refresco automatico del pane cada pocos segundos, asi que el
    caso normal —nada nuevo— tiene que ser barato: son dos consultas, la del
    ticket y la de los ids posteriores a `after`, y devuelve `comments: []`.
    Solo cuando hay algo nuevo se paga prepare_comments, que necesita la
    conversacion entera para deduplicar las citas de los correos.

    La visibilidad es la misma que en add_comment: quien no es agente no ve los
    comentarios internos, ni aqui ni al publicar.
    """
    ticket = get_object_or_404(Ticket.objects.select_related('assignee'), id=ticket_id)
    if not can_view_ticket(request.user, ticket):
        return json_error("forbidden", "No tienes acceso a este ticket.", status=403)

    try:
        after = int(request.GET.get('after') or 0)
    except (TypeError, ValueError):
        after = 0

    visible = ticket.comment_set.all()
    if not _can_view_all(request.user):
        visible = visible.filter(is_public=True)

    new_ids = set(visible.filter(id__gt=after).values_list('id', flat=True))
    comments = []
    if new_ids:
        presented = prepare_comments(list(
            visible.select_related('user', 'user__role').order_by('created_at', 'id')
        ))
        comments = [_comment_payload(c, ticket) for c in presented if c.pk in new_ids]

    return JsonResponse({
        'ticket': _ticket_header_payload(ticket),
        'comments': comments,
        'last_comment_id': max(new_ids) if new_ids else after,
    })


@api_login_required
@require_POST
def ai_suggest_reply(request, ticket_id):
    """Asistencia IA: borradores de respuesta al cliente a partir del ticket,
    sus comentarios públicos y casos similares resueltos. No persiste nada."""
    from app.services.ticket_reply import TicketReplyService

    ticket = get_object_or_404(Ticket, id=ticket_id)
    if not permission_is_agent(request.user):
        return json_error("forbidden", "Solo agentes pueden usar la asistencia de IA.", status=403)
    if ticket.is_deleted or ticket.status == 'closed':
        return json_error('ticket_locked', 'El ticket está eliminado o cerrado.', status=409)
    if not TicketReplyService.can_use(request.user):
        return json_error("ai_disabled", "La asistencia de respuesta IA no está disponible.", status=503)
    try:
        data = parse_json_body(request)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)

    instruction = (data.get("instruction") or "")[:1000]
    try:
        result = TicketReplyService.suggest(ticket, instruction=instruction)
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).exception("ai_suggest_reply failed ticket=%s", ticket_id)
        return json_error("ai_error", f"No se pudo generar la respuesta: {exc}", status=502)
    return json_ok(result)


@api_login_required
def update_user_notes(request, user_id):
    if request.method != 'POST':
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    if not _is_agent(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    import json as _json
    try:
        body = _json.loads(request.body)
    except ValueError:
        return JsonResponse({'error': 'Invalid JSON'}, status=400)
    user = get_object_or_404(User, pk=user_id)
    user.notes = (body.get('notes') or '').strip() or None
    user.save(update_fields=['notes'])
    return JsonResponse({'ok': True})


@api_login_required
def notifications_api(request):
    notifs = NotificationService.unread_for_user(request.user)
    data = [
        {
            "id": n.id,
            "message": n.message,
            "ticket_id": n.ticket_id,
            "created_at": timezone.localtime(n.created_at).strftime("%d/%m %H:%M"),
        }
        for n in notifs
    ]
    # `unread` es el total real: la lista viene recortada y el contador de la
    # campana se quedaría clavado en el límite.
    return JsonResponse({
        "notifications": data,
        "unread": NotificationService.unread_count(request.user),
    })

@api_login_required
def recent_activity_api(request):
    """Conversaciones en las que la última palabra no es tuya.

    No es «lo último que he tocado»: eso subía al principio el ticket que
    acababas de responder y no se iba nunca al leerlo. Aquí solo entra el
    ticket cuyo último comentario público es de la otra parte, así que
    desaparece de la lista en cuanto contestas.
    """
    # Sin corte temporal, los tickets importados de Zendesk asoman respuestas
    # de hace años.
    recent_cutoff = timezone.now() - timedelta(days=30)
    base = Ticket.objects.filter(
        merged_into__isnull=True, is_deleted=False,
    ).exclude(status="closed")

    last_public = Comment.objects.filter(
        ticket=OuterRef("pk"), is_public=True,
    ).order_by("-created_at", "-id")
    if _can_view_all(request.user):
        base = base.filter(assignee=request.user)
    else:
        base = base.filter(Q(requester=request.user) | Q(ccs=request.user)).distinct()

    tickets = base.annotate(
        last_comment_at=Subquery(last_public.values("created_at")[:1]),
        last_comment_user=Subquery(last_public.values("user_id")[:1]),
    ).filter(last_comment_at__gte=recent_cutoff)

    if _can_view_all(request.user):
        # La última palabra es del cliente: se excluye a la plantilla entera,
        # no solo a uno mismo, para que la respuesta de un compañero tampoco
        # cuente como pendiente de contestar.
        tickets = tickets.exclude(
            last_comment_user__in=User.objects.filter(agent_user_filter()).values("id")
        )
    else:
        tickets = tickets.exclude(last_comment_user=request.user.id)

    tickets = tickets.select_related("requester", "assignee").order_by("-last_comment_at")[:10]
    authors = {
        user.id: user.name
        for user in User.objects.filter(
            id__in={t.last_comment_user for t in tickets if t.last_comment_user}
        ).only("id", "name")
    }
    data = [
        {
            "ticket_id": t.id,
            "subject": t.subject,
            "replied_at": timezone.localtime(t.last_comment_at).strftime("%d/%m %H:%M"),
            "replied_by": authors.get(t.last_comment_user, ""),
            "status": t.status,
            "priority": t.priority or "",
            "requester": t.requester.name if t.requester else "",
            "assignee": t.assignee.name if t.assignee else "",
        }
        for t in tickets
    ]
    return JsonResponse({"activity": data})

def _notify_users(ticket, message, actor=None):
    return NotificationService.notify_ticket_users(ticket, message, actor=actor)

@api_login_required
@require_POST
def mark_notification_read(request, notif_id):
    notif = get_object_or_404(Notification, id=notif_id, user=request.user)
    notif.read = True
    notif.save()
    return JsonResponse({"ok": True, "unread": NotificationService.unread_count(request.user)})


@api_login_required
@require_POST
def mark_all_notifications_read(request):
    cleared = Notification.objects.filter(user=request.user, read=False).update(read=True)
    return JsonResponse({"ok": True, "cleared": cleared,
                         "unread": NotificationService.unread_count(request.user)})


@api_login_required
@require_POST
def mark_ticket_notifications_read(request, ticket_id):
    """Abrir el ticket cuenta como haber leído sus avisos.

    Si no, el contador solo bajaba pulsando el item del panel y quedaba
    inflado en cuanto el agente entraba al ticket por la tabla o la pestaña.
    """
    updated = NotificationService.mark_ticket_read(request.user, ticket_id)
    return JsonResponse({
        "ok": True, "cleared": updated,
        "unread": NotificationService.unread_count(request.user),
    })

@api_login_required
@require_POST
def upload_attachment(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    if not can_view_ticket(request.user, ticket):
        return json_error('forbidden', 'No tienes acceso a este ticket.', status=403)
    if ticket.is_deleted or ticket.status == 'closed':
        return json_error('ticket_locked', 'El ticket está eliminado o cerrado.', status=409)
    try:
        att = AttachmentService.upload(_single_attachment_file(request), request.user, ticket=ticket,
                                       upload_key=request.POST.get('upload_key'), draft_key=request.POST.get('draft_key'),
                                       pending_ids=request.POST.get('pending_ids'))
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)

    return JsonResponse(AttachmentService.metadata(att))


def _single_attachment_file(request):
    files = [f for key in request.FILES for f in request.FILES.getlist(key)]
    if len(files) != 1 or len(request.FILES.getlist('file')) != 1:
        raise APIValidationError('invalid_file', 'Se requiere exactamente un archivo en file.', status=400)
    return files[0]


@api_login_required
@require_POST
def upload_draft_attachment(request):
    try:
        att = AttachmentService.upload(_single_attachment_file(request), request.user,
                                       draft_key=request.POST.get('draft_key'), upload_key=request.POST.get('upload_key'),
                                       pending_ids=request.POST.get('pending_ids'))
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)
    return JsonResponse(AttachmentService.metadata(att))


@api_login_required
@require_POST
def cancel_attachment_upload(request):
    try:
        data = parse_json_body(request)
        if not isinstance(data, dict):
            raise APIValidationError('invalid_json', 'Se requiere un objeto JSON.', status=400)
        AttachmentService.cancel_upload(request.user, data.get('upload_key'),
                                        draft_key=data.get('draft_key'), ticket_id=data.get('ticket_id'))
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)
    return json_ok({'cancelled': True, 'upload_key': data['upload_key']})


@api_login_required
@require_POST
def delete_pending_attachment(request, attachment_id):
    try:
        AttachmentService.delete_pending(attachment_id, request.user)
    except Attachment.DoesNotExist:
        return json_error('attachment_not_found', 'Adjunto no encontrado.', status=404)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)
    return json_ok({'deleted': True, 'attachment_id': attachment_id})

@api_login_required
@require_GET
def macros_api(request):
    if not _is_agent(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    macros = Macro.objects.filter(active=True).order_by('name').values('id', 'name', 'description', 'actions')
    return JsonResponse({'macros': list(macros)})


# 'closed' solo lo pone el sistema (#21384): las macros cierran con on_hold.
_MACRO_STATUS_CHOICES = {'open', 'pending', 'resolved', 'on_hold'}
_MACRO_PRIORITY_CHOICES = {'low', 'normal', 'high', 'urgent'}


def _serialize_macro(m):
    return {
        'id': m.id,
        'name': m.name,
        'description': m.description or '',
        'actions': m.actions or {},
        'active': m.active,
        'zendesk_id': m.zendesk_id,
    }


def _macro_actions_from_payload(data):
    """Construye el dict `actions` a partir del payload, validando enums.

    Solo incluye claves con valor; omite las vacías para no escribir campos en
    blanco al aplicar la macro. Devuelve (actions, error) — error es un
    JsonResponse si algo no valida.
    """
    actions = {}
    status = (data.get('status') or '').strip().lower()
    if status:
        if status not in _MACRO_STATUS_CHOICES:
            return None, json_error('invalid_status', 'status no válido', status=400)
        actions['status'] = status
    priority = (data.get('priority') or '').strip().lower()
    if priority:
        if priority not in _MACRO_PRIORITY_CHOICES:
            return None, json_error('invalid_priority', 'priority no válida', status=400)
        actions['priority'] = priority
    assignee_id = _as_int_or_none(data.get('assignee_id'))
    if assignee_id:
        actions['assignee_id'] = assignee_id
    comment = (data.get('comment') or '').strip()
    if comment:
        actions['comment'] = comment
    return actions, None


@api_login_required
def macros_manage_api(request):
    """CRUD de macros para agentes. Las macros son compartidas (sin dueño)."""
    if not _is_agent(request.user):
        return json_error('forbidden', 'No autorizado', status=403)

    if request.method == 'GET':
        macros = Macro.objects.order_by('-active', 'name')
        return JsonResponse({'ok': True, 'macros': [_serialize_macro(m) for m in macros]})

    try:
        data = parse_json_body(request)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)

    if request.method in ('POST', 'PUT'):
        macro = Macro.objects.filter(id=data.get('id')).first() if request.method == 'PUT' else Macro()
        if request.method == 'PUT' and not macro:
            return json_error('macro_not_found', 'Macro no encontrada', status=404)

        name = (data.get('name') or '').strip()
        if not name:
            return json_error('name_required', 'El nombre es obligatorio', status=400)

        actions, err = _macro_actions_from_payload(data)
        if err:
            return err
        if not actions:
            return json_error('actions_required', 'La macro debe tener al menos una acción', status=400)

        macro.name = name[:255]
        macro.description = ((data.get('description') or '').strip()[:500]) or None
        macro.actions = actions
        macro.active = bool(data.get('active', True))
        macro.save()
        return JsonResponse({'ok': True, 'macro': _serialize_macro(macro)})

    if request.method == 'DELETE':
        macro_id = _as_int_or_none(request.GET.get('id'))
        macro = Macro.objects.filter(id=macro_id).first()
        if not macro:
            return json_error('macro_not_found', 'Macro no encontrada', status=404)
        macro.delete()
        return JsonResponse({'ok': True, 'deleted': True})

    return json_error('method_not_allowed', 'Método no permitido', status=405)


@api_login_required
@require_GET
def global_search(request):
    q = (request.GET.get("q") or "").strip()
    if not q:
        return JsonResponse({"tickets": [], "users": []})

    ticket_qs = (
        Ticket.objects.filter(merged_into__isnull=True, is_deleted=False)
        if _can_view_all(request.user) else
        Ticket.objects.filter(merged_into__isnull=True, is_deleted=False)
        .filter(Q(requester=request.user) | Q(ccs=request.user) | Q(created_by=request.user))
        .distinct()
    )

    # Handle #ID (e.g. "#454") or plain digit string — prioritise exact ID match
    raw = q.lstrip('#')
    if raw.isdigit():
        id_val = int(raw)
        exact = list(ticket_qs.filter(id=id_val).select_related('requester', 'assignee'))
        if q.startswith('#'):
            # User explicitly asked for a ticket by ID — return only the exact match
            tickets = exact
        else:
            # Numeric string without '#': exact ID hit first, then text matches
            text = list(
                ticket_qs.filter(
                    Q(subject__icontains=q) |
                    Q(requester__name__icontains=q) |
                    Q(requester__email__icontains=q) |
                    Q(assignee__name__icontains=q) |
                    Q(assignee__email__icontains=q)
                ).exclude(id=id_val).select_related('requester', 'assignee').distinct()[:9]
            )
            tickets = exact + text
    else:
        # Text search — description excluded for performance and to avoid false positives
        tickets = list(
            ticket_qs.filter(
                Q(subject__icontains=q) |
                Q(service__icontains=q) |
                Q(requester__name__icontains=q) |
                Q(requester__email__icontains=q) |
                Q(assignee__name__icontains=q) |
                Q(assignee__email__icontains=q) |
                Q(tags__name__icontains=q)
            ).select_related('requester', 'assignee').distinct()[:10]
        )

    tickets_data = [{
        "id": t.id,
        "display_id": t.zendesk_id or t.id,
        "subject": t.subject,
        "status": t.status,
        "requester": t.requester.name if t.requester else "-",
        "assignee": t.assignee.name if t.assignee else "-",
        "service": t.service or "-"
    } for t in tickets]

    # Users (agents/admins only)
    if _can_view_all(request.user):
        users = list(
            User.objects.filter(
                Q(name__icontains=q) |
                Q(email__icontains=q) |
                Q(role__role_name__icontains=q) |
                Q(group__group_name__icontains=q)
            ).select_related('role', 'group').distinct()[:10]
        )
        users_data = [{
            "id": u.id,
            "name": u.name,
            "email": u.email,
            "role": u.role.role_name if u.role else "-",
            "group": u.group.group_name if u.group else "-"
        } for u in users]
    else:
        users_data = []

    return JsonResponse({"tickets": tickets_data, "users": users_data})


def _merge_message(data, prefix):
    """Comentario revisado en el paso 2 del modal de fusión, o None si no viene."""
    if f'{prefix}_comment' not in data:
        return None
    return {
        'content': data.get(f'{prefix}_comment', ''),
        'html_body': data.get(f'{prefix}_comment_html', ''),
        'is_public': data.get(f'{prefix}_is_public') == 'true',
    }


def _merge_message_json(message):
    """Igual que _merge_message, para el cuerpo JSON de la fusión en bloque."""
    if not isinstance(message, dict):
        return None
    return {
        'content': str(message.get('content') or ''),
        'html_body': str(message.get('html_body') or ''),
        'is_public': message.get('is_public') is True,
    }


@api_login_required
@require_POST
def merge_ticket(request, ticket_id):
    try:
        target_id = int(request.POST.get('target_ticket_id', ''))
        target = MergeService.merge_ticket(
            ticket_id, target_id, request.user,
            source_message=_merge_message(request.POST, 'source'),
            target_message=_merge_message(request.POST, 'target'),
        )
    except (ValueError, TypeError):
        return json_error('invalid_target', 'Ticket destino no encontrado', status=404)
    except Ticket.DoesNotExist:
        return json_error('ticket_not_found', 'Ticket no encontrado', status=404)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)
    return JsonResponse({'ok': True, 'data': {'target_id': target.id}, 'target_id': target.id})


@api_login_required
@require_POST
def bulk_delete(request):
    if not _is_agent(request.user):
        return json_error('forbidden', 'Solo agentes pueden gestionar la papelera.', status=403)
    try:
        ids = _ticket_trash_request_ids(request)
        updated = TicketTrashService.delete(request.user, ids)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)
    return JsonResponse({'ok': True, 'deleted': updated})


@api_login_required
@require_POST
def bulk_restore(request):
    if not _is_agent(request.user):
        return json_error('forbidden', 'Solo agentes pueden gestionar la papelera.', status=403)
    try:
        ids = _ticket_trash_request_ids(request)
        updated = TicketTrashService.restore(request.user, ids)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)
    return JsonResponse({'ok': True, 'restored': updated})


def _ticket_trash_request_ids(request):
    data = parse_json_body(request)
    if not isinstance(data, dict):
        raise APIValidationError('invalid_json', 'Se requiere un objeto JSON.', status=400)
    return data.get('ids')


@api_login_required
@require_POST
def bulk_merge(request):
    try:
        data = parse_json_body(request)
        ids = data.get('ids', [])
        target_id = int(data.get('target_id', 0))
    except (APIValidationError, AttributeError, ValueError, TypeError) as exc:
        if isinstance(exc, APIValidationError):
            return json_error(exc.code, exc.message, status=exc.status)
        return json_error('invalid_payload', 'Payload inválido', status=400)
    if not ids or not target_id:
        return json_error('missing_fields', 'ids y target_id requeridos', status=400)

    try:
        target, merged_count = MergeService.bulk_merge(
            ids, target_id, request.user,
            source_message=_merge_message_json(data.get('source_message')),
            target_message=_merge_message_json(data.get('target_message')),
        )
    except Ticket.DoesNotExist:
        return json_error('target_not_found', 'Ticket destino no encontrado', status=404)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)
    return JsonResponse({'ok': True, 'data': {'target_id': target.id, 'merged': merged_count}, 'target_id': target.id, 'merged': merged_count})

@login_required
def documentation(request):
    """Render comprehensive documentation for TicketFlow."""
    return render(request, 'documentation.html')


# ---------------------------------------------------------------------------
# Panel de administración (solo rol admin)
# ---------------------------------------------------------------------------

def _admin_required(view_func):
    """Decorator: 401/403 JSON para APIs; HTML conserva redirect/403."""
    from functools import wraps

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            if request.path.startswith('/api/'):
                return json_error('not_authenticated', 'Authentication required', status=401)
            return redirect('login')
        if not can_manage_users(request.user):
            # Para vistas API devolver JSON; para vistas HTML, renderizar 403
            if request.path.startswith('/api/'):
                return json_error('forbidden', 'Forbidden', status=403)
            return render(request, "tickets/403.html",
                          {"error": "Solo los administradores pueden acceder a esta sección."},
                          status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped


def _admin_field_options(field, choices):
    """Opciones para los selects de ambito de una regla de asignacion.

    La taxonomia de `choices` mas los valores que de hecho existen en tickets y
    no estan en ella (Ticket.service arrastra texto libre de Zendesk): sin esto
    una regla no podria acotarse a un servicio heredado, o peor, editar la regla
    perderia en silencio el valor que ya tenia por no estar entre las opciones.
    """
    options = [{'value': value, 'label': label} for value, label in choices]
    known = {value for value, _ in choices}
    extras = (
        Ticket.objects.filter(is_deleted=False)
        .exclude(**{f'{field}__isnull': True})
        .exclude(**{field: ''})
        .values_list(field, flat=True)
        .distinct()
        .order_by(field)
    )
    options.extend({'value': extra, 'label': extra} for extra in extras if extra not in known)
    return options


@_admin_required
def admin_panel(request):
    users = (
        User.objects
        .select_related('role', 'group')
        .order_by('name')
    )
    roles = Role.objects.order_by('role_name')
    groups = Group.objects.order_by('group_name')
    brands = Brand.objects.exclude(name='').order_by('name')
    # Candidatos del reparto: los mismos que usa AssignmentService de fallback,
    # para que el selector de la pestana «Asignacion» no ofrezca end users.
    agents = AssignmentService.agent_queryset().order_by('name')
    return render(request, 'admin_panel.html', {
        'services': _admin_field_options('service', SERVICE_CHOICES),
        'channels': _admin_field_options('channel', CHANNEL_CHOICES),
        'users': users,
        'roles': roles,
        'groups': groups,
        'brands': brands,
        'agents': agents,
        'username': request.user.name,
        'email': request.user.email,
    })


@_admin_required
def admin_users_api(request):
    """CRUD de usuarios para administradores.

    GET  /api/admin/users/            → lista (opcional ?q=, ?role_id=, ?group_id=)
    POST /api/admin/users/            → crea {name, email, role_id, group_id, password?}
    PUT  /api/admin/users/            → actualiza {id, name?, email?, role_id?, group_id?, is_active?}
    DELETE /api/admin/users/?id=N     → desactiva (soft-delete) o borra si no tiene tickets

    Lógica de negocio (validaciones y mutaciones) en `services.user_admin.UserAdminService`.
    """
    if request.method == 'GET':
        return JsonResponse(UserAdminService.list_users(request.GET))

    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    try:
        if request.method == 'POST':
            user = UserAdminService.create_user(data)
            return JsonResponse({'ok': True, 'user': UserAdminService.serialize(user)})

        if request.method == 'PUT':
            try:
                user_id = int(data.get('id') or 0)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'id requerido'}, status=400)
            user = UserAdminService.update_user(request.user, user_id, data)
            return JsonResponse({'ok': True, 'user': UserAdminService.serialize(user)})

        if request.method == 'DELETE':
            try:
                user_id = int(request.GET.get('id') or 0)
            except (TypeError, ValueError):
                return JsonResponse({'error': 'id requerido'}, status=400)
            result = UserAdminService.delete_user(request.user, user_id)
            return JsonResponse({'ok': True, **result})
    except APIValidationError as exc:
        return JsonResponse({'error': exc.message}, status=exc.status)

    return JsonResponse({'error': 'Método no permitido'}, status=405)


@_admin_required
def admin_groups_api(request):
    """CRUD de grupos (solo admin)."""
    if request.method == 'GET':
        data = [
            {
                'id': g.id,
                'group_name': g.group_name,
                'description': g.description or '',
                'user_count': User.objects.filter(group=g).count(),
            }
            for g in Group.objects.order_by('group_name')
        ]
        return JsonResponse({'groups': data})

    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    if request.method == 'POST':
        name = (data.get('group_name') or '').strip()
        if not name:
            return JsonResponse({'error': 'group_name requerido'}, status=400)
        if Group.objects.filter(group_name__iexact=name).exists():
            return JsonResponse({'error': 'Ya existe un grupo con ese nombre'}, status=400)
        g = Group.objects.create(group_name=name, description=(data.get('description') or '').strip() or None)
        return JsonResponse({'ok': True, 'group': {'id': g.id, 'group_name': g.group_name}})

    if request.method == 'PUT':
        try:
            gid = int(data.get('id') or 0)
        except (TypeError, ValueError):
            return JsonResponse({'error': 'id requerido'}, status=400)
        g = Group.objects.filter(id=gid).first()
        if not g:
            return JsonResponse({'error': 'Grupo no encontrado'}, status=404)
        if 'group_name' in data:
            new_name = (data.get('group_name') or '').strip()
            if not new_name:
                return JsonResponse({'error': 'group_name no puede estar vacío'}, status=400)
            if Group.objects.filter(group_name__iexact=new_name).exclude(id=g.id).exists():
                return JsonResponse({'error': 'Ya existe otro grupo con ese nombre'}, status=400)
            g.group_name = new_name
        if 'description' in data:
            g.description = (data.get('description') or '').strip() or None
        g.save()
        return JsonResponse({'ok': True, 'group': {'id': g.id, 'group_name': g.group_name}})

    if request.method == 'DELETE':
        try:
            gid = int(request.GET.get('id') or 0)
        except (TypeError, ValueError):
            return JsonResponse({'error': 'id requerido'}, status=400)
        g = Group.objects.filter(id=gid).first()
        if not g:
            return JsonResponse({'error': 'Grupo no encontrado'}, status=404)
        # Cualquier usuario que esté en este grupo queda sin grupo (SET_NULL ya lo hace)
        g.delete()
        return JsonResponse({'ok': True, 'deleted': True})

    return JsonResponse({'error': 'Método no permitido'}, status=405)


@_admin_required
def admin_roles_api(request):
    """CRUD de roles (solo admin)."""
    # Los permisos dependen del nombre: renombrar uno de estos los rompería.
    _PROTECTED_ROLES = {'admin', 'administrator', 'administrador', 'end user', 'end-user', 'solo lectura'}

    if request.method == 'GET':
        data = [
            {
                'id': r.id,
                'role_name': r.role_name,
                'description': r.description or '',
                'user_count': User.objects.filter(role=r).count(),
                'protected': r.role_name.lower() in _PROTECTED_ROLES,
            }
            for r in Role.objects.order_by('role_name')
        ]
        return JsonResponse({'roles': data})

    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    if request.method == 'POST':
        name = (data.get('role_name') or '').strip()
        if not name:
            return JsonResponse({'error': 'role_name requerido'}, status=400)
        if Role.objects.filter(role_name__iexact=name).exists():
            return JsonResponse({'error': 'Ya existe un rol con ese nombre'}, status=400)
        r = Role.objects.create(role_name=name, description=(data.get('description') or '').strip() or None)
        return JsonResponse({'ok': True, 'role': {'id': r.id, 'role_name': r.role_name}})

    if request.method == 'PUT':
        try:
            rid = int(data.get('id') or 0)
        except (TypeError, ValueError):
            return JsonResponse({'error': 'id requerido'}, status=400)
        r = Role.objects.filter(id=rid).first()
        if not r:
            return JsonResponse({'error': 'Rol no encontrado'}, status=404)
        if r.role_name.lower() in _PROTECTED_ROLES:
            return JsonResponse({'error': 'Este rol es del sistema y no puede modificarse'}, status=400)
        if 'role_name' in data:
            new_name = (data.get('role_name') or '').strip()
            if not new_name:
                return JsonResponse({'error': 'role_name no puede estar vacío'}, status=400)
            if Role.objects.filter(role_name__iexact=new_name).exclude(id=r.id).exists():
                return JsonResponse({'error': 'Ya existe otro rol con ese nombre'}, status=400)
            r.role_name = new_name
        if 'description' in data:
            r.description = (data.get('description') or '').strip() or None
        r.save()
        return JsonResponse({'ok': True, 'role': {'id': r.id, 'role_name': r.role_name}})

    if request.method == 'DELETE':
        try:
            rid = int(request.GET.get('id') or 0)
        except (TypeError, ValueError):
            return JsonResponse({'error': 'id requerido'}, status=400)
        r = Role.objects.filter(id=rid).first()
        if not r:
            return JsonResponse({'error': 'Rol no encontrado'}, status=404)
        if r.role_name.lower() in _PROTECTED_ROLES:
            return JsonResponse({'error': 'Este rol es del sistema y no puede borrarse'}, status=400)
        if User.objects.filter(role=r).exists():
            return JsonResponse({'error': 'No puedes borrar un rol con usuarios asignados'}, status=400)
        r.delete()
        return JsonResponse({'ok': True, 'deleted': True})

    return JsonResponse({'error': 'Método no permitido'}, status=405)


@_admin_required
@require_POST
def admin_reset_password_api(request, user_id):
    """Permite al admin establecer una contraseña nueva o invalidarla."""
    user = get_object_or_404(User, id=user_id)
    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)
    new_password = (data.get('password') or '').strip()
    if new_password:
        if len(new_password) < 8:
            return JsonResponse({'error': 'La contraseña debe tener al menos 8 caracteres'}, status=400)
        user.set_password(new_password)
    else:
        user.set_unusable_password()
    user.save()
    return JsonResponse({'ok': True})


def _as_int_or_none(value):
    try:
        return int(value) if value not in (None, '') else None
    except (TypeError, ValueError):
        return None


def _as_scope_list(value):
    """Normaliza una dimension de ambito a lista de cadenas sin vacios ni duplicados."""
    if value in (None, ''):
        return []
    items = value if isinstance(value, (list, tuple)) else [value]
    cleaned = []
    for item in items:
        text = str(item).strip()
        if text and text not in cleaned:
            cleaned.append(text)
    return cleaned


def _serialize_assignment_rule(rule):
    return {
        'id': rule.id,
        'name': rule.name,
        'active': rule.active,
        'group_ids': [group.id for group in rule.groups.all()],
        'group_names': [group.group_name for group in rule.groups.all()],
        'services': list(rule.services or []),
        'channels': list(rule.channels or []),
        'members': [
            {
                'id': member.id,
                'user_id': member.user_id,
                'user_name': member.user.name if member.user else '',
                'weight': member.weight,
                'capacity': member.capacity,
                'active': member.active,
            }
            for member in rule.members.select_related('user').order_by('user__name')
        ],
    }


@_admin_required
def admin_assignment_rules_api(request):
    if request.method == 'GET':
        rules = AssignmentRule.objects.prefetch_related('members__user', 'groups').order_by('name')
        return JsonResponse({'ok': True, 'rules': [_serialize_assignment_rule(rule) for rule in rules]})

    try:
        data = parse_json_body(request)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)

    if request.method in ('POST', 'PUT'):
        rule = AssignmentRule.objects.filter(id=data.get('id')).first() if request.method == 'PUT' else AssignmentRule()
        if request.method == 'PUT' and not rule:
            return json_error('assignment_rule_not_found', 'Regla de asignacion no encontrada', status=404)
        name = (data.get('name') or '').strip()
        if not name:
            return json_error('name_required', 'name requerido', status=400)
        rule.name = name
        rule.active = bool(data.get('active', True))
        rule.services = _as_scope_list(data.get('services'))
        rule.channels = _as_scope_list(data.get('channels'))
        rule.save()

        group_ids = [
            group_id
            for group_id in (_as_int_or_none(item) for item in _as_scope_list(data.get('group_ids')))
            if group_id is not None
        ]
        rule.groups.set(Group.objects.filter(id__in=group_ids))

        if 'members' in data:
            rule.members.all().delete()
            for item in data.get('members') or []:
                user_id = _as_int_or_none(item.get('user_id'))
                if not user_id:
                    continue
                AssignmentRuleMember.objects.create(
                    rule=rule,
                    user_id=user_id,
                    weight=max(_as_int_or_none(item.get('weight')) or 1, 1),
                    capacity=_as_int_or_none(item.get('capacity')),
                    active=bool(item.get('active', True)),
                )
        return JsonResponse({'ok': True, 'rule': _serialize_assignment_rule(rule)})

    if request.method == 'DELETE':
        rule_id = _as_int_or_none(request.GET.get('id'))
        rule = AssignmentRule.objects.filter(id=rule_id).first()
        if not rule:
            return json_error('assignment_rule_not_found', 'Regla de asignacion no encontrada', status=404)
        rule.active = False
        rule.save(update_fields=['active'])
        return JsonResponse({'ok': True, 'deactivated': True})

    return json_error('method_not_allowed', 'Metodo no permitido', status=405)


def _serialize_sla_policy(policy):
    return {
        'id': policy.id,
        'name': policy.name,
        'active': policy.active,
        'priority': policy.priority or '',
        'service': policy.service or '',
        'brand_id': policy.brand_id,
        'brand_name': policy.brand.name if policy.brand_id and policy.brand else '',
        'assigned_group_id': policy.assigned_group_id,
        'first_response_minutes': policy.first_response_minutes,
        'resolution_minutes': policy.resolution_minutes,
    }


@_admin_required
def admin_sla_policies_api(request):
    if request.method == 'GET':
        policies = SLAPolicy.objects.select_related('assigned_group', 'brand').order_by('name')
        return JsonResponse({'ok': True, 'policies': [_serialize_sla_policy(policy) for policy in policies]})

    try:
        data = parse_json_body(request)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)

    if request.method in ('POST', 'PUT'):
        policy = SLAPolicy.objects.filter(id=data.get('id')).first() if request.method == 'PUT' else SLAPolicy()
        if request.method == 'PUT' and not policy:
            return json_error('sla_policy_not_found', 'Politica SLA no encontrada', status=404)
        name = (data.get('name') or '').strip()
        if not name:
            return json_error('name_required', 'name requerido', status=400)
        policy.name = name
        policy.active = bool(data.get('active', True))
        policy.priority = (data.get('priority') or '').strip() or None
        policy.service = (data.get('service') or '').strip() or None
        policy.brand_id = _as_int_or_none(data.get('brand_id'))
        policy.assigned_group_id = _as_int_or_none(data.get('assigned_group_id'))
        policy.first_response_minutes = max(_as_int_or_none(data.get('first_response_minutes')) or 0, 0)
        policy.resolution_minutes = max(_as_int_or_none(data.get('resolution_minutes')) or 0, 0)
        policy.save()
        return JsonResponse({'ok': True, 'policy': _serialize_sla_policy(policy)})

    if request.method == 'DELETE':
        policy_id = _as_int_or_none(request.GET.get('id'))
        policy = SLAPolicy.objects.filter(id=policy_id).first()
        if not policy:
            return json_error('sla_policy_not_found', 'Politica SLA no encontrada', status=404)
        policy.active = False
        policy.save(update_fields=['active'])
        return JsonResponse({'ok': True, 'deactivated': True})

    return json_error('method_not_allowed', 'Metodo no permitido', status=405)


def _serialize_response_template(tpl):
    return {
        'id': tpl.id,
        'key': tpl.key,
        'brand_id': tpl.brand_id,
        'brand_name': tpl.brand.name if tpl.brand_id and tpl.brand else '',
        'language': tpl.language,
        'subject': tpl.subject,
        'body_html': tpl.body_html,
        'body_text': tpl.body_text,
        'active': tpl.active,
    }


_TEMPLATE_PREVIEW_CONTEXT = {
    'ticket_id': 12345,
    'subject': 'Ejemplo: no puedo acceder al portal',
    'requester_name': 'María Ejemplo',
    'requester_email': 'maria@example.com',
    'brand_name': 'Mi Marca',
    'support_email': 'helpdesk@example.com',
    'from_name': 'Soporte Mi Marca',
    'ticket_url': 'https://ticketflow.example.com/tickets/create/?id=12345',
    'created_at': '10/06/2026 15:30',
    'agent_name': 'Agente Ejemplo',
    'reply_html': '<p>Hemos revisado tu solicitud y ya puedes volver a acceder.</p>',
    'reply_text': 'Hemos revisado tu solicitud y ya puedes volver a acceder.',
    'responded_at': '10/06/2026 16:10',
}


@_admin_required
def admin_response_templates_api(request):
    from app.services.email_templates import ResponseTemplateService

    if request.method == 'GET':
        templates = ResponseTemplate.objects.select_related('brand').order_by('key', 'brand__name', 'language')
        return JsonResponse({'ok': True, 'templates': [_serialize_response_template(t) for t in templates]})

    try:
        data = parse_json_body(request)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)

    if request.method == 'POST' and request.GET.get('action') == 'preview':
        # Renderiza subject/body con contexto de ejemplo sin guardar nada.
        class _Tmp:
            subject = (data.get('subject') or '')
            body_html = sanitize_email_html(data.get('body_html') or '')
            body_text = (data.get('body_text') or '')
        try:
            rendered = ResponseTemplateService.render(_Tmp, _TEMPLATE_PREVIEW_CONTEXT)
        except Exception as exc:
            return json_error('template_render_error', f'Error de plantilla: {exc}', status=400)
        return JsonResponse({'ok': True, 'preview': rendered})

    if request.method in ('POST', 'PUT'):
        tpl = ResponseTemplate.objects.filter(id=data.get('id')).first() if request.method == 'PUT' else ResponseTemplate()
        if request.method == 'PUT' and not tpl:
            return json_error('template_not_found', 'Plantilla no encontrada', status=404)

        key = slugify((data.get('key') or '').strip())
        if not key:
            return json_error('key_required', 'key requerida', status=400)
        language = (data.get('language') or 'es').strip().lower()
        if language not in ('es', 'en'):
            return json_error('invalid_language', "language debe ser 'es' o 'en'", status=400)
        subject = (data.get('subject') or '').strip()
        body_html = sanitize_email_html(data.get('body_html') or '')
        if not subject or not body_html:
            return json_error('subject_body_required', 'subject y body_html son obligatorios', status=400)
        if key in {'ticket_created', 'agent_reply', 'satisfaction_survey'} and '{{ticket_id}}' not in subject.replace(' ', ''):
            # El token [Ticket #N] del subject es el mecanismo de threading de
            # las respuestas; avisamos pero no bloqueamos.
            logger.warning('response_template %s sin {{ticket_id}} en subject', key)

        brand_id = _as_int_or_none(data.get('brand_id'))
        duplicate = ResponseTemplate.objects.filter(
            key=key, brand_id=brand_id, language=language
        ).exclude(id=tpl.id or 0).select_related('brand').first()
        reused = False
        if duplicate:
            if request.method == 'POST' and not duplicate.active:
                # Desactivar no borra, asi que la fila sigue ocupando la clave y
                # crear "la misma" plantilla era un callejon sin salida: se revive
                # esa fila con los datos nuevos. En PUT no, ahi fusionar dos filas
                # existentes seria destructivo y el aviso es lo correcto.
                tpl = duplicate
                reused = True
            else:
                # La key se normaliza con slugify, asi que hay que decir cual quedo.
                marca = duplicate.brand.name if duplicate.brand_id and duplicate.brand else 'Global'
                return json_error(
                    'duplicate_template',
                    f'Ya existe una plantilla con la clave "{key}" para '
                    f'{marca} en {language.upper()}.',
                    status=400,
                )

        tpl.key = key
        tpl.brand_id = brand_id
        tpl.language = language
        tpl.subject = subject[:255]
        tpl.body_html = body_html
        tpl.body_text = (data.get('body_text') or '').strip()
        tpl.active = bool(data.get('active', True))
        tpl.save()
        return JsonResponse({'ok': True, 'template': _serialize_response_template(tpl), 'reused': reused})

    if request.method == 'DELETE':
        tpl_id = _as_int_or_none(request.GET.get('id'))
        tpl = ResponseTemplate.objects.filter(id=tpl_id).first()
        if not tpl:
            return json_error('template_not_found', 'Plantilla no encontrada', status=404)
        tpl.active = False
        tpl.save(update_fields=['active'])
        return JsonResponse({'ok': True, 'deactivated': True})

    return json_error('method_not_allowed', 'Metodo no permitido', status=405)


def _serialize_satisfaction_reason(reason):
    return {
        'id': reason.id,
        'code': reason.code,
        'label': reason.label,
        'language': reason.language,
        'position': reason.position,
        'active': reason.active,
    }


@_admin_required
def admin_satisfaction_reasons_api(request):
    """Catalogo de motivos de voto negativo de la encuesta de satisfaccion."""
    if request.method == 'GET':
        reasons = SatisfactionReason.objects.order_by('language', 'position', 'label')
        return JsonResponse({'ok': True, 'reasons': [_serialize_satisfaction_reason(r) for r in reasons]})

    if request.method == 'DELETE':
        # Desactivar, no borrar: las valoraciones ya emitidas apuntan al motivo y
        # borrarlo dejaria el historico sin explicacion (FK con SET_NULL).
        reason_id = _as_int_or_none(request.GET.get('id'))
        reason = SatisfactionReason.objects.filter(id=reason_id).first()
        if not reason:
            return json_error('reason_not_found', 'Motivo no encontrado', status=404)
        reason.active = False
        reason.save(update_fields=['active'])
        return JsonResponse({'ok': True, 'deactivated': True})

    try:
        data = parse_json_body(request)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)

    if request.method in ('POST', 'PUT'):
        reason = (SatisfactionReason.objects.filter(id=data.get('id')).first()
                  if request.method == 'PUT' else SatisfactionReason())
        if request.method == 'PUT' and not reason:
            return json_error('reason_not_found', 'Motivo no encontrado', status=404)

        label = (data.get('label') or '').strip()
        if not label:
            return json_error('label_required', 'label requerida', status=400)
        language = (data.get('language') or 'es').strip().lower()
        if language not in ('es', 'en'):
            return json_error('invalid_language', "language debe ser 'es' o 'en'", status=400)
        # El code identifica el motivo entre idiomas; si no lo dan, se deriva de la etiqueta.
        code = slugify((data.get('code') or label).strip())[:64]
        if not code:
            return json_error('code_required', 'code requerido', status=400)
        duplicate = SatisfactionReason.objects.filter(
            code=code, language=language
        ).exclude(id=reason.id or 0).first()
        reused = False
        if duplicate:
            if request.method == 'POST' and not duplicate.active:
                # Mismo caso que en las plantillas: el code de un motivo
                # desactivado sigue ocupado, asi que crear "el mismo" motivo
                # revive esa fila en vez de dejar al admin sin salida.
                reason = duplicate
                reused = True
            else:
                # El code puede venir derivado de la etiqueta, asi que hay que
                # decir cual es o el aviso no se entiende.
                return json_error(
                    'duplicate_reason',
                    f'Ya existe el motivo "{duplicate.label}" con el codigo '
                    f'"{code}" en {language.upper()}.',
                    status=400,
                )

        reason.code = code
        reason.label = label[:255]
        reason.language = language
        reason.position = max(_as_int_or_none(data.get('position')) or 0, 0)
        reason.active = bool(data.get('active', True))
        reason.save()
        return JsonResponse({'ok': True, 'reason': _serialize_satisfaction_reason(reason), 'reused': reused})

    return json_error('method_not_allowed', 'Metodo no permitido', status=405)


def _serialize_automation_rule(rule):
    return {
        'id': rule.id,
        'name': rule.name,
        'active': rule.active,
        'priority': rule.priority,
        'conditions': rule.conditions,
        'actions': rule.actions,
    }


@_admin_required
def admin_automation_rules_api(request):
    if request.method == 'GET':
        rules = AutomationRule.objects.order_by('priority', 'name')
        return JsonResponse({'ok': True, 'rules': [_serialize_automation_rule(rule) for rule in rules]})

    try:
        data = parse_json_body(request)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)

    if request.method in ('POST', 'PUT'):
        rule = AutomationRule.objects.filter(id=data.get('id')).first() if request.method == 'PUT' else AutomationRule()
        if request.method == 'PUT' and not rule:
            return json_error('automation_rule_not_found', 'Automatizacion no encontrada', status=404)
        name = (data.get('name') or '').strip()
        if not name:
            return json_error('name_required', 'name requerido', status=400)
        rule.name = name
        rule.active = bool(data.get('active', True))
        rule.priority = max(_as_int_or_none(data.get('priority')) or 100, 0)
        rule.conditions = data.get('conditions') or {}
        rule.actions = data.get('actions') or {}
        rule.save()
        return JsonResponse({'ok': True, 'rule': _serialize_automation_rule(rule)})

    if request.method == 'DELETE':
        rule_id = _as_int_or_none(request.GET.get('id'))
        rule = AutomationRule.objects.filter(id=rule_id).first()
        if not rule:
            return json_error('automation_rule_not_found', 'Automatizacion no encontrada', status=404)
        rule.active = False
        rule.save(update_fields=['active'])
        return JsonResponse({'ok': True, 'deactivated': True})

    return json_error('method_not_allowed', 'Metodo no permitido', status=405)


# ---------------------------------------------------------------------------
# Encuesta de satisfaccion (publica, sin autenticacion)
# ---------------------------------------------------------------------------

@ensure_csrf_cookie
def satisfaction_survey(request, token):
    """Pagina publica de voto de la encuesta de satisfaccion.

    SIN @login_required a proposito: el destinatario es un cliente final que no
    tiene cuenta. La credencial es el token de 32 bytes del enlace. No hace falta
    csrf_exempt: el formulario incluye {% csrf_token %} y la cookie se emite en el
    GET, que funciona igual para un anonimo.
    """
    from app.services.email_templates import ResponseTemplateService

    rating = SatisfactionService.rating_for_token(token)
    ticket = rating.ticket if rating and rating.ticket_id else None

    def _render(state, error=None, score=None):
        language = ResponseTemplateService.pick_language(ticket) if ticket else 'es'
        from app.models import HelpCenter
        center = None
        if ticket:
            center = HelpCenter.objects.filter(active=True, service__iexact=ticket.service or '').first()
            if not center and ticket.brand_id:
                center = HelpCenter.objects.filter(active=True, brand_id=ticket.brand_id).first()
        return render(request, 'tickets/satisfaction_survey.html', {
            'state': state,
            'error': error,
            'rating': rating,
            'ticket': ticket,
            # Sin nombre de marca no se pone nada: el cliente final no conoce
            # «TicketFlow», que es el nombre interno de la herramienta.
            'brand_name': center.name if center else (ticket.brand.name if ticket and ticket.brand_id and ticket.brand else ''),
            'center': center,
            'language': language,
            'brand_style': f'--brand-primary:{center.primary_color};--brand-accent:{center.accent_color}' if center else '',
            'reasons': SatisfactionService.reasons_for(language) if state == 'form' else [],
            'selected_score': score,
        }, status=404 if state == 'invalid' else 200)

    # Token desconocido, o valoracion cuyo ticket se borro (ticket es SET_NULL).
    if rating is None or ticket is None or ticket.is_deleted:
        return _render('invalid')

    if rating.is_expired:
        # Si ya habia votado, darle las gracias es mejor que un error seco.
        return _render('done' if rating.is_answered else 'expired')

    if request.method == 'POST':
        score = (request.POST.get('score') or '').strip().lower()
        if score not in ('good', 'bad'):
            language = ResponseTemplateService.pick_language(ticket) if ticket else 'es'
            error = 'Choose one of the two options to continue.' if language == 'en' else 'Elige una de las dos opciones para continuar.'
            return _render('form', error=error)
        reason = None
        if score == 'bad':
            reason_id = _as_int_or_none(request.POST.get('reason_id'))
            if reason_id:
                reason = SatisfactionReason.objects.filter(id=reason_id, active=True).first()
        SatisfactionService.record_vote(
            rating, score, comment=request.POST.get('comment'), reason=reason,
        )
        return _render('done')

    # Un clic en el boton del email trae ?score=, que deja la opcion preseleccionada
    # y reduce el voto a confirmar. Si ya voto, se preselecciona lo que voto.
    requested = (request.GET.get('score') or '').strip().lower()
    if requested not in ('good', 'bad'):
        requested = rating.score if rating.is_answered else None
    return _render('form', score=requested)


@api_login_required
@require_POST
def ticket_presence(request, ticket_id):
    if not _is_agent(request.user):
        return json_error('forbidden', 'La presencia solo está disponible para agentes.', status=403)
    ticket = get_object_or_404(Ticket, pk=ticket_id)
    try:
        if request.content_type == 'application/json':
            data = parse_json_body(request)
            if not isinstance(data, dict):
                raise APIValidationError('invalid_json', 'Se requiere un objeto JSON.', status=400)
            composing = data.get('composing', False)
        else:
            data = request.POST
            raw = data.get('composing', 'false')
            if raw not in ('true', 'false', '1', '0'):
                raise APIValidationError('invalid_composing', 'composing debe ser un booleano.', status=400)
            composing = raw in ('true', '1')
        action = data.get('action', 'heartbeat')
        if action not in ('heartbeat', 'leave'):
            raise APIValidationError('invalid_action', 'Acción de presencia no válida.', status=400)
        seen_at = None
        if action == 'leave' and data.get('seen_at'):
            raw = data['seen_at']
            try:
                seen_at = parse_datetime(raw) if isinstance(raw, str) else None
            except ValueError:
                seen_at = None
            if seen_at is None or timezone.is_naive(seen_at):
                raise APIValidationError('invalid_seen_at', 'seen_at debe incluir fecha, hora y zona.', status=400)
        payload = ticket_presence_heartbeat(ticket, request.user, composing=composing,
                                            leave=action == 'leave', seen_at=seen_at)
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)
    return json_ok(payload)
