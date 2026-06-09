"""
Definition of views.
"""
# -*- coding: utf-8 -*-
from django.db import transaction
from django.db.models import Q, OuterRef, Subquery, Count, Max, Case, When, IntegerField
from django.utils.dateparse import parse_datetime
from datetime import datetime, timedelta
from types import new_class
from unicodedata import category
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from .models import Brand, Organization, Ticket, Comment, User, TicketTag, Attachment, TicketEvent, Notification, Role, Group, Macro, SatisfactionRating, AssignmentRule, AssignmentRuleMember, SLAPolicy, AutomationRule
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
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.utils.text import slugify
from django.conf import settings
import os, time
from django.conf import settings as DJANGO_SETTINGS
from django.utils import timezone
from django.views import View
from django.views.decorators.http import require_GET
from django.core.cache import cache
from .api import APIValidationError, api_login_required, json_error, json_ok, parse_json_body
from .permissions import can_manage_users, can_update_ticket, can_view_ticket, is_admin as permission_is_admin, is_agent as permission_is_agent
from .sanitizers import sanitize_email_html
from .services.assignment import AssignmentService
from .services.comments import CommentService
from .services.merge import MergeService
from .services.notifications import NotificationService
from .services.tickets import TicketService

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

_TICKET_SORT_FIELDS = {
    'id':         'id',
    'subject':    'subject',
    'requester':  'requester__name',
    'updated_at': 'updated_at',
    'service':    'service',
    'assignee':   'assignee__name',
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
    'open':    {'exclude': ['closed', 'resolved']},
    'pending': {'filter': ['pending']},
    'solved':  {'filter': ['closed', 'resolved']},
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
        t.lc_author = lc.user.name if lc and lc.user else ''
        t.lc_date   = lc.created_at.strftime('%d/%m/%Y %H:%M') if lc else ''
        t.lc_body   = (lc.content or '')[:300] if lc else ''


def _parse_page_size(raw):
    try:
        size = int(raw or _DEFAULT_PAGE_SIZE)
        return size if size in _ALLOWED_PAGE_SIZES else _DEFAULT_PAGE_SIZE
    except (ValueError, TypeError):
        return _DEFAULT_PAGE_SIZE


def _ticket_filter_counts_cache_key(user):
    role_name = (getattr(getattr(user, 'role', None), 'role_name', '') or '').lower()
    return (
        f"tickets:filter-counts:v2:user:{user.id}:"
        f"role:{role_name}:group:{user.group_id or 0}"
    )


def _base_tickets_queryset(user):
    qs = Ticket.objects.filter(merged_into__isnull=True, is_deleted=False)
    if _is_agent(user):
        return qs.order_by('-updated_at')
    return qs.filter(Q(requester=user) | Q(ccs=user)).distinct().order_by('-updated_at')


def _tickets_for_view(base_qs, view, user):
    now = timezone.now()
    if view == "mis_tickets":
        return base_qs
    if view == "telefonica_mes":
        return base_qs.filter(
            requester__organization__name="Telefonica",
            created_at__gte=now - timedelta(days=30),
        )
    if view == "unsolved_no_tareas":
        return base_qs.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea"))
    if view == "unassigned":
        return base_qs.filter(assignee__isnull=True)
    if view == "all_unsolved_no_tareas":
        return base_qs.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea"))
    if view == "recently_updated":
        return base_qs.filter(updated_at__gte=now - timedelta(hours=24))
    if view == "recently_solved":
        return base_qs.filter(status="resolved")
    if view == "pendientes":
        return base_qs.filter(status="pending")
    if view == "tareas":
        return base_qs.filter(type="tarea")
    if view == "unsolved_groups":
        return base_qs.filter(~Q(status__in=["closed", "resolved"]), assignee__group=user.group)
    if view == "rated_last7":
        since = now - timedelta(days=7)
        rated_ids = SatisfactionRating.objects.filter(
            created_at__gte=since, score__in=['good', 'bad']
        ).values_list('ticket_id', flat=True)
        return base_qs.filter(id__in=rated_ids)
    if view == "internos_comuny":
        return base_qs.filter(service="Comunycarse", status="open")
    if view == "abiertos_ecomfax":
        return base_qs.filter(service="ecomfax", status="open")
    if view == "recordia_sgsd":
        return base_qs.filter(service="Recordia SGSD", status="open")
    if view == "closed":
        return base_qs.filter(status="closed")
    if view == "sus_pendientes":
        return base_qs.filter(requester=user, status="pending")
    if view == "espera":
        return base_qs.filter(status="espera") if hasattr(Ticket, "espera") else base_qs.none()
    if view == "abiertos":
        return base_qs.filter(status="open")
    if view == "sus_no_cerrados":
        if _is_agent(user):
            return base_qs.filter(requester=user).exclude(status="closed")
        return base_qs.exclude(status="closed")
    if view == "ultimos_cerrados":
        return base_qs.filter(status="closed")
    if view == "no_resueltos":
        return base_qs.exclude(status="resolved")
    if view == "twitter":
        return base_qs.filter(channel="twitter")
    if view == "twitter_dm":
        return base_qs.filter(channel="twitter_dm")
    if view == "twitter_like":
        return base_qs.filter(channel="twitter_like")
    if view == "sus_tareas":
        return base_qs.filter(requester=user, type="tarea")
    if view == "resueltos":
        return base_qs.filter(status="resolved")
    if view == "new_in_groups":
        return base_qs.filter(assignee__group=user.group, created_at__gte=now - timedelta(days=7))
    if view == "open":
        return base_qs.filter(status="open")
    if view == "no_update_48h":
        return base_qs.filter(updated_at__lte=now - timedelta(hours=48))
    return base_qs


@login_required
def home(request):
    user = request.user

    tickets_qs = Ticket.objects.filter(
        Q(requester=user) | Q(assignee=user) | Q(ccs=user)
    ).distinct()

    # --- Tickets Abiertos ---
    abiertos_you = tickets_qs.filter(status="open").count()
    abiertos_groups = 0
    if user.group:
        abiertos_groups = Ticket.objects.filter(
            assignee__group=user.group, status="open"
        ).distinct().count()

    cutoff_60  = timezone.now() - timedelta(days=60)
    solventado = tickets_qs.filter(
        status__in=["closed", "resolved"],
        updated_at__gte=cutoff_60,
    ).count()

    # Annotate each ticket with its latest good/bad satisfaction score
    rating_sub = SatisfactionRating.objects.filter(
        ticket=OuterRef('pk'), score__in=['good', 'bad']
    ).order_by('-created_at').values('score')[:1]

    tickets_list = list(
        tickets_qs
        .exclude(status__in=['closed', 'resolved'])
        .annotate(satisfaction_score=Subquery(rating_sub))
        .select_related('requester', 'assignee')
        .order_by('-updated_at')
        [:100]
    )

    _attach_last_comments(tickets_list)

    # Personal bien/mal: tickets del usuario, últimos 60 días
    user_ratings_60 = SatisfactionRating.objects.filter(
        ticket__in=tickets_qs,
        created_at__gte=cutoff_60,
        score__in=['good', 'bad'],
    )
    bien = user_ratings_60.filter(score='good').count()
    mal  = user_ratings_60.filter(score='bad').count()

    # Satisfacción global (todos los agentes, últimos 60 días)
    global_60   = SatisfactionRating.objects.filter(
        created_at__gte=cutoff_60, score__in=['good', 'bad']
    )
    bien_g      = global_60.filter(score='good').count()
    mal_g       = global_60.filter(score='bad').count()
    total_rated = bien_g + mal_g
    satisfaccion_pct = round((bien_g / total_rated) * 100) if total_rated > 0 else None

    context = {
        "username": user.name,
        "email": user.email,
        "tickets": tickets_list,
        "my_tickets": tickets_list,
        "current_user_id": user.id,
        "current_user_group_id": user.group_id,
        "stats": {
            "abiertos_you": abiertos_you,
            "abiertos_groups": abiertos_groups,
            "bien": bien,
            "mal": mal,
            "solventado": solventado,
            "satisfaccion_pct": satisfaccion_pct,
            "total_rated": total_rated,
        }
    }
    return render(request, "tickets/home.html", context)

@login_required
def tickets_list(request):
    # Los filtros se cargan de forma asíncrona desde filter_tickets?counts=1
    return render(request, "tickets/tickets_list.html", {})

@api_login_required
def filter_tickets(request):
    """
    Devuelve tickets paginados en JSON según el 'view' seleccionado.
    Parámetros: view, page (1-based), counts=1 (opcional, activa el cálculo de contadores).
    """
    view = request.GET.get("view")
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

    base_qs = _base_tickets_queryset(request.user)

    if total_only:
        total = _tickets_for_view(base_qs, view, request.user).count()
        return JsonResponse({
            "pagination": {
                "total": total,
                "total_pages": max(1, (total + page_size - 1) // page_size),
                "page_size": page_size,
            },
            "filtros": {view: total} if view else {},
        })

    # Los contadores son costosos. Solo se calculan cuando counts=1.
    # Consolidamos ~25 COUNTs separados en 5 queries para reducir round-trips.
    filtros = {}
    _counts_cache_key = None
    _counts_loaded_from_cache = False
    if compute_counts:
        _counts_cache_key = _ticket_filter_counts_cache_key(request.user)
        if not force_counts:
            cached_filtros = cache.get(_counts_cache_key)
            if cached_filtros is not None:
                filtros = cached_filtros
                compute_counts = False
                _counts_loaded_from_cache = True

    if compute_counts:
        _now = timezone.now()

        # Query 1: aggregate condicional — una sola pasada sobre la tabla base
        # cubre todos los contadores basados en status/type/channel/service/assignee.
        _user_group = request.user.group
        _agg = base_qs.aggregate(
            mis_tickets      = Count('id'),
            n_open           = Count(Case(When(status='open',     then=1), output_field=IntegerField())),
            n_pending        = Count(Case(When(status='pending',  then=1), output_field=IntegerField())),
            n_closed         = Count(Case(When(status='closed',   then=1), output_field=IntegerField())),
            n_resolved       = Count(Case(When(status='resolved', then=1), output_field=IntegerField())),
            n_tarea          = Count(Case(When(type='tarea',      then=1), output_field=IntegerField())),
            n_twitter        = Count(Case(When(channel='twitter',      then=1), output_field=IntegerField())),
            n_twitter_dm     = Count(Case(When(channel='twitter_dm',   then=1), output_field=IntegerField())),
            n_twitter_like   = Count(Case(When(channel='twitter_like', then=1), output_field=IntegerField())),
            n_comuny         = Count(Case(When(service='Comunycarse',    status='open', then=1), output_field=IntegerField())),
            n_ecomfax        = Count(Case(When(service='ecomfax',        status='open', then=1), output_field=IntegerField())),
            n_recordia       = Count(Case(When(service='Recordia SGSD',  status='open', then=1), output_field=IntegerField())),
            n_unassigned     = Count(Case(When(assignee__isnull=True,    then=1), output_field=IntegerField())),
            n_unsolved_notarea = Count(Case(
                When(~Q(status__in=['closed', 'resolved']) & ~Q(type='tarea'), then=1),
                output_field=IntegerField()
            )),
            n_no_resueltos   = Count(Case(When(~Q(status='resolved'), then=1), output_field=IntegerField())),
        )

        # Query 2: contadores dependientes del usuario actual (requester/group)
        _user_agg = base_qs.filter(requester=request.user).aggregate(
            sus_pendientes = Count(Case(When(status='pending', then=1), output_field=IntegerField())),
            sus_tareas     = Count(Case(When(type='tarea',     then=1), output_field=IntegerField())),
            sus_no_cerrados_agent = Count(Case(When(~Q(status='closed'), then=1), output_field=IntegerField())),
        )

        # Query 3: filtros de tiempo (updated_at / created_at ranges)
        _time_agg = base_qs.aggregate(
            recently_updated = Count(Case(When(updated_at__gte=_now - timedelta(hours=24), then=1), output_field=IntegerField())),
            no_update_48h    = Count(Case(When(updated_at__lte=_now - timedelta(hours=48), then=1), output_field=IntegerField())),
        )

        # Query 4: Telefónica (JOIN a organization — no se puede incluir en aggregate base)
        n_telefonica = base_qs.filter(
            requester__organization__name="Telefonica",
            created_at__gte=_now - timedelta(days=30),
        ).count()

        # Query 5: grupos del usuario actual (requires group join)
        n_unsolved_groups = 0
        n_new_in_groups   = 0
        if _user_group:
            n_unsolved_groups = base_qs.filter(
                ~Q(status__in=['closed', 'resolved']),
                assignee__group=_user_group,
            ).count()
            n_new_in_groups = base_qs.filter(
                assignee__group=_user_group,
                created_at__gte=_now - timedelta(days=7),
            ).count()

        sus_no_cerrados_count = (
            (base_qs.exclude(status='closed').count() if not _is_agent(request.user)
             else _user_agg['sus_no_cerrados_agent'])
        )

        filtros = {
            "mis_tickets":        _agg['mis_tickets'],
            "telefonica_mes":     n_telefonica,
            "unsolved_no_tareas": _agg['n_unsolved_notarea'],
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
            "espera":             0,
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
        }

    if _counts_cache_key and filtros and not _counts_loaded_from_cache:
        cache.set(_counts_cache_key, filtros, _TICKET_FILTER_COUNTS_TTL)

    if counts_only:
        return JsonResponse({"filtros": filtros})

    tickets = _tickets_for_view(base_qs, view, request.user)

    db_sort = _TICKET_SORT_FIELDS.get(sort_by)
    db_sort_signed = (f'-{db_sort}' if sort_dir == 'desc' else db_sort) if db_sort else None

    # Si el usuario ha pedido ordenar por una columna, desactivamos la agrupación
    # para mostrar la tabla plana ordenada por su criterio.
    if db_sort_signed:
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
    elif db_sort_signed:
        tickets = tickets.order_by(db_sort_signed)

    offset = (page - 1) * page_size
    page_qs = tickets.select_related('requester', 'assignee', 'assigned_group')
    exact_total = True
    has_next = False

    if fast_pagination:
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
            return t.assignee.name if t.assignee else '— Sin asignar —'
        if group_by == 'status':
            return (t.status or '—').capitalize()
        if group_by == 'group':
            return t.assigned_group.group_name if t.assigned_group else '— Sin grupo —'
        if group_by == 'requester':
            return t.requester.name if t.requester else '—'
        return None

    data = []
    for t in tickets_page:
        lc = last_comments.get(t.id)
        data.append({
            "id": t.id,
            "subject": t.subject,
            "requester": t.requester.name if t.requester else "-",
            "updated_at": t.updated_at.strftime("%d/%m/%Y %H:%M"),
            "service": t.service or "-",
            "assignee": t.assignee.name if t.assignee else "-",
            "status": t.status,
            "priority": t.priority or "",
            "description": (t.description or "")[:200],
            "group_value": _group_value(t),
            "last_comment": {
                "author": lc.user.name if lc and lc.user else "",
                "date": lc.created_at.strftime("%d/%m/%Y %H:%M") if lc else "",
                "body": (lc.content or "")[:300] if lc else "",
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
    })

@api_login_required
def filter_customers(request):
    if not _is_agent(request.user) and not _is_admin(request.user):
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

    base_queryset = User.objects.select_related('role', 'group').order_by('name')
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
        users = users.order_by(f'-{db_sort}' if sort_dir == 'desc' else db_sort)

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
            "created_at": u.created_at.strftime("%d/%m/%Y %H:%M"),
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
    if not _is_agent(request.user) and not _is_admin(request.user):
        return render(request, "tickets/403.html", {"error": "No tienes permisos para ver este perfil."}, status=403)
    customer_id = request.GET.get("id")
    customer = get_object_or_404(User, id=customer_id)

    base_qs = Ticket.objects.filter(
        Q(requester__id=customer.id) | Q(assignee__id=customer.id) |
        Q(ccs__id=customer.id) | Q(created_by_id=customer.id)
    ).distinct()
    ctx = _profile_tickets_context(request, base_qs)
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
    if not _is_agent(request.user) and not _is_admin(request.user):
         return render(request, "tickets/403.html", {"error": "No tienes permisos para ver clientes."}, status=403)

    role_qs = Role.objects.all().order_by('role_name')
    group_qs = Group.objects.all().order_by('group_name')
    if not _is_admin(request.user):
        role_qs = role_qs.exclude(role_name__in=['admin', 'administrator'])

    context = {
        'username': request.user.name,
        'email': request.user.email,
        'is_admin': _is_admin(request.user),
        'roles': role_qs,
        'groups': group_qs,
    }
    return render(request, "tickets/customers_list.html", context)

@login_required
def reporting(request):
    if not _is_agent(request.user) and not _is_admin(request.user):
        return render(request, "tickets/403.html", {"error": "No tienes permisos para ver los reportes."}, status=403)
    context = {
        'username': request.user.name,
        'email': request.user.email,
    }
    return render(request, "tickets/reporting.html", context)

@login_required
def settings(request):
    if not _is_agent(request.user) and not _is_admin(request.user):
        return render(request, "tickets/403.html", {"error": "No tienes permisos para acceder a la configuración."}, status=403)
    context = {
        'username': request.user.name,
        'email': request.user.email,
    }
    return render(request, "tickets/settings.html", context)
   
@login_required
def profile(request):
    user = request.user
    base_qs = Ticket.objects.filter(
        Q(requester__id=user.id) | Q(assignee__id=user.id) |
        Q(ccs__id=user.id) | Q(created_by_id=user.id)
    ).distinct()
    ctx = _profile_tickets_context(request, base_qs)
    ctx["user"] = user
    return render(request, "tickets/profile.html", ctx)

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
        # claims = data.get('claims', []) # Aquí puedes leer claims si los envías

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
        Ticket.objects.select_related('brand', 'requester', 'assignee'),
        id=ticket_id,
    )
    if not can_view_ticket(request.user, ticket):
        return json_error("forbidden", "No tienes acceso a este ticket.", status=403)

    # Marcar como leídas las notificaciones del usuario para este ticket
    Notification.objects.filter(user=request.user, ticket=ticket, read=False).update(read=True)

    data = {
        'empresa': ticket.brand.name if ticket.brand else '',
        'solicitante': ticket.requester_id,
        'solicitante_name': ticket.requester.name if ticket.requester else '',
        'asignado': ticket.assignee_id,
        'asignado_name': ticket.assignee.name if ticket.assignee else '',
        'grupo': ticket.assigned_group_id,
        # ccs: list of {id, name} objects so the frontend can pre-populate AJAX Select2
        'ccs': [{'id': u.id, 'name': u.name} for u in ticket.ccs.all()],
        'tags': [tag.id for tag in ticket.tags.all()],
        'tipo': ticket.type,
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
    if not _is_agent(request.user) and not _is_admin(request.user):
        qs = qs.filter(id=request.user.id)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q))

    offset = (page - 1) * page_size
    users = list(qs[offset:offset + page_size])
    has_more = len(users) == page_size  # if full page, there might be more

    return JsonResponse({
        'results': [{'id': u.id, 'text': u.name} for u in users],
        'pagination': {'more': has_more},
    })


def _is_agent(user):
    return permission_is_agent(user)

def _is_admin(user):
    return permission_is_admin(user)

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
        return redirect(f'{request.path}?id={ticket.id}')

    # GET .
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
                   .filter(requester_id=ticket_obj.requester_id)
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
                'updated_at': t.updated_at.strftime('%d/%m/%Y %H:%M'),
                'description': desc_snippet,
                'last_comment': snippet,
            })
    # Build combined feed: comments + audit events, sorted by created_at
    _FIELD_LABEL = {
        'status': 'estado', 'assignee_id': 'asignado', 'group_id': 'grupo',
        'priority': 'prioridad', 'tags': 'tags', 'subject': 'asunto',
        'requester_id': 'solicitante',
    }
    if ticket_obj:
        if _is_agent(request.user):
            comments_list = list(
                ticket_obj.comment_set.select_related('user', 'user__role').order_by('created_at')
            )
        else:
            comments_list = list(
                ticket_obj.comment_set.filter(is_public=True)
                .select_related('user', 'user__role').order_by('created_at')
            )
        events_list = list(ticket_obj.events.select_related('actor').order_by('created_at'))

        feed = []
        for c in comments_list:
            feed.append({'item_type': 'comment', 'obj': c})
        for e in events_list:
            feed.append({'item_type': 'event', 'obj': e,
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
        'grupos': grupos,
        'username': request.user.name,
        'email': request.user.email,
        'ticket': ticket_obj,
        'feed': feed,
        'can_use_internal': _is_agent(request.user),
        'user_timeline': user_timeline,
        'ticket_satisfaction': ticket_satisfaction,
        'current_user_id': request.user.id,
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
    if ticket.assignee_id == request.user.id:
        return JsonResponse({'ok': False, 'success': False, 'error': 'already_assigned'})
    with transaction.atomic():
        ticket = Ticket.objects.select_for_update().get(id=ticket_id)
        prev = str(ticket.assignee_id) if ticket.assignee_id else None
        ticket.assignee = request.user
        ticket.save(update_fields=['assignee'])
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

@api_login_required
@require_POST
def add_comment(request, ticket_id):
    try:
        data = parse_json_body(request)
        ticket = get_object_or_404(Ticket, id=ticket_id)
        comment, applied_status = CommentService.add_comment(
            ticket=ticket,
            actor=request.user,
            content=data.get('content') or '',
            is_public=data.get('is_public', True),
            html_body=data.get('html_body') or '',
            new_status=data.get('new_status') or '',
            attachment_ids=data.get('attachment_ids') or [],
        )
    except APIValidationError as exc:
        return json_error(exc.code, exc.message, status=exc.status)

    # adjuntos del comentario para el frontend
    atts = Attachment.objects.filter(comment=comment).values('id', 'file_url', 'file_type')
    return JsonResponse({
        'id': comment.id,
        'content': comment.content,
        'html_body': comment.html_body or '',
        'created_at': comment.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'created_at_iso': comment.created_at.isoformat(),
        'ticket_id': ticket.id,
        'user_id': request.user.id,
        'username': request.user.name,
        'user_role': request.user.role.role_name if request.user.role else None,
        'is_public': comment.is_public,
        'attachments': list(atts),
        'new_status': applied_status,
    })
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
            "created_at": n.created_at.strftime("%d/%m %H:%M"),
        }
        for n in notifs
    ]
    return JsonResponse({"notifications": data})

@api_login_required
def recent_activity_api(request):
    # Solo actualizaciones de los últimos 30 días — sin este corte los tickets
    # importados de Zendesk muestran updated_at de hace años en el dashboard.
    recent_cutoff = timezone.now() - timedelta(days=30)
    base = Ticket.objects.filter(
        merged_into__isnull=True,
        is_deleted=False,
        updated_at__gte=recent_cutoff,
    )
    if _is_agent(request.user):
        tickets = (
            base
            .filter(Q(assignee=request.user) | Q(created_by=request.user))
            .select_related('requester', 'assignee')
            .order_by('-updated_at')
            .distinct()[:10]
        )
    else:
        tickets = (
            base
            .filter(Q(requester=request.user) | Q(ccs=request.user) | Q(created_by=request.user))
            .select_related('requester', 'assignee')
            .order_by('-updated_at')
            .distinct()[:10]
        )
    data = [
        {
            "ticket_id": t.id,
            "subject": t.subject,
            "updated_at": t.updated_at.strftime("%d/%m %H:%M") if t.updated_at else "",
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
    return JsonResponse({"ok": True})

@api_login_required
@require_POST
def upload_attachment(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    if not can_view_ticket(request.user, ticket):
        return json_error('forbidden', 'No tienes acceso a este ticket.', status=403)
    f = request.FILES.get('file')
    if not f:
        return JsonResponse({'error': 'No se recibió ningún archivo.'}, status=400)

    # Nombre seguro + carpeta por ticket
    root, ext = os.path.splitext(f.name)
    safe_name = f"{slugify(root)[:80]}{ext.lower()}"
    rel_dir = f"attachments/tickets/{ticket.id}/"
    rel_path = os.path.join(rel_dir, f"{int(time.time())}_{safe_name}")

    # Guardar a disco
    saved_path = default_storage.save(rel_path, ContentFile(f.read()))
    raw_url = default_storage.url(saved_path)
    # build_absolute_uri solo si la URL es relativa (local dev); S3 ya devuelve URL absoluta
    file_url = raw_url if raw_url.startswith('http') else request.build_absolute_uri(raw_url)

    att = Attachment.objects.create(
        file_url=file_url,
        file_type=getattr(f, 'content_type', None),
        ticket=ticket,
        uploaded_by=request.user,
    )

    return JsonResponse({
        'id': att.id,
        'file_url': att.file_url,
        'file_type': att.file_type or '',
        'filename': safe_name,
        'size': getattr(f, 'size', 0),
    })

@api_login_required
@require_GET
def macros_api(request):
    if not _is_agent(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    macros = Macro.objects.filter(active=True).order_by('name').values('id', 'name', 'description', 'actions')
    return JsonResponse({'macros': list(macros)})


@api_login_required
@require_GET
def global_search(request):
    q = (request.GET.get("q") or "").strip()
    if not q:
        return JsonResponse({"tickets": [], "users": []})

    ticket_qs = (
        Ticket.objects.filter(merged_into__isnull=True, is_deleted=False)
        if _is_agent(request.user) else
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
        "subject": t.subject,
        "status": t.status,
        "requester": t.requester.name if t.requester else "-",
        "assignee": t.assignee.name if t.assignee else "-",
        "service": t.service or "-"
    } for t in tickets]

    # Users (agents/admins only)
    if _is_agent(request.user):
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


@api_login_required
@require_POST
def merge_ticket(request, ticket_id):
    try:
        target_id = int(request.POST.get('target_ticket_id', ''))
        target = MergeService.merge_ticket(ticket_id, target_id, request.user)
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
        return JsonResponse({'error': 'Permiso denegado'}, status=403)
    try:
        ids = json.loads(request.body).get('ids', [])
    except (json.JSONDecodeError, AttributeError):
        return JsonResponse({'error': 'Payload inválido'}, status=400)
    if not ids or not isinstance(ids, list):
        return JsonResponse({'error': 'ids requerido'}, status=400)
    updated = Ticket.objects.filter(
        id__in=ids, merged_into__isnull=True, is_deleted=False
    ).update(is_deleted=True)
    return JsonResponse({'ok': True, 'deleted': updated})


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
        target, merged_count = MergeService.bulk_merge(ids, target_id, request.user)
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

import re as _re_admin
_EMAIL_RE = _re_admin.compile(r'^[^@\s]+@[^@\s]+\.[^@\s]+$')


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


@_admin_required
def admin_panel(request):
    users = (
        User.objects
        .select_related('role', 'group')
        .order_by('name')
    )
    roles = Role.objects.order_by('role_name')
    groups = Group.objects.order_by('group_name')
    return render(request, 'admin_panel.html', {
        'users': users,
        'roles': roles,
        'groups': groups,
        'username': request.user.name,
        'email': request.user.email,
    })


def _admin_serialize_user(u):
    return {
        'id': u.id,
        'name': u.name,
        'email': u.email,
        'role_id': u.role_id,
        'role_name': u.role.role_name if u.role else None,
        'group_id': u.group_id,
        'group_name': u.group.group_name if u.group else None,
        'is_active': bool(u.is_active),
        'created_at': u.created_at.strftime('%d/%m/%Y') if u.created_at else '',
    }


@_admin_required
def admin_users_api(request):
    """CRUD de usuarios para administradores.

    GET  /api/admin/users/            → lista (opcional ?q=, ?role_id=, ?group_id=)
    POST /api/admin/users/            → crea {name, email, role_id, group_id, password?}
    PUT  /api/admin/users/            → actualiza {id, name?, email?, role_id?, group_id?, is_active?}
    DELETE /api/admin/users/?id=N     → desactiva (soft-delete) o borra si no tiene tickets
    """
    if request.method == 'GET':
        qs = User.objects.select_related('role', 'group').order_by('name')
        q = (request.GET.get('q') or '').strip()
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(email__icontains=q))
        role_id = request.GET.get('role_id')
        group_id = request.GET.get('group_id')
        if role_id and role_id.isdigit():
            qs = qs.filter(role_id=int(role_id))
        if group_id and group_id.isdigit():
            qs = qs.filter(group_id=int(group_id))
        # Paginación server-side
        try:
            page = max(1, int(request.GET.get('page') or 1))
        except (ValueError, TypeError):
            page = 1
        try:
            page_size = int(request.GET.get('page_size') or 50)
            if page_size not in (25, 50, 100, 200):
                page_size = 50
        except (ValueError, TypeError):
            page_size = 50
        total = qs.count()
        total_pages = max(1, (total + page_size - 1) // page_size)
        page = min(page, total_pages)
        offset = (page - 1) * page_size
        users = list(qs[offset:offset + page_size])
        return JsonResponse({
            'users': [_admin_serialize_user(u) for u in users],
            'pagination': {
                'total': total, 'page': page,
                'total_pages': total_pages, 'page_size': page_size,
            },
        })

    try:
        data = json.loads(request.body or '{}')
    except ValueError:
        return JsonResponse({'error': 'JSON inválido'}, status=400)

    if request.method == 'POST':
        name = (data.get('name') or '').strip()
        email = (data.get('email') or '').strip().lower()
        if not name:
            return JsonResponse({'error': 'El nombre es obligatorio'}, status=400)
        if not _EMAIL_RE.match(email):
            return JsonResponse({'error': 'Email no válido'}, status=400)
        if User.objects.filter(email__iexact=email).exists():
            return JsonResponse({'error': 'Ya existe un usuario con ese email'}, status=400)

        role = None
        if data.get('role_id'):
            role = Role.objects.filter(id=data['role_id']).first()
            if not role:
                return JsonResponse({'error': 'Rol no encontrado'}, status=400)
        group = None
        if data.get('group_id'):
            group = Group.objects.filter(id=data['group_id']).first()
            if not group:
                return JsonResponse({'error': 'Grupo no encontrado'}, status=400)

        user = User(email=email, name=name, role=role, group=group, is_active=True)
        password = (data.get('password') or '').strip()
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save()
        return JsonResponse({'ok': True, 'user': _admin_serialize_user(user)})

    if request.method == 'PUT':
        try:
            user_id = int(data.get('id') or 0)
        except (TypeError, ValueError):
            return JsonResponse({'error': 'id requerido'}, status=400)
        if not user_id:
            return JsonResponse({'error': 'id requerido'}, status=400)
        user = User.objects.filter(id=user_id).first()
        if not user:
            return JsonResponse({'error': 'Usuario no encontrado'}, status=404)

        # No permitir que un admin se desactive a sí mismo (riesgo lockout)
        if user.id == request.user.id and data.get('is_active') is False:
            return JsonResponse({'error': 'No puedes desactivar tu propia cuenta'}, status=400)

        if 'name' in data:
            name = (data.get('name') or '').strip()
            if not name:
                return JsonResponse({'error': 'El nombre no puede estar vacío'}, status=400)
            user.name = name

        if 'email' in data:
            new_email = (data.get('email') or '').strip().lower()
            if not _EMAIL_RE.match(new_email):
                return JsonResponse({'error': 'Email no válido'}, status=400)
            if User.objects.filter(email__iexact=new_email).exclude(id=user.id).exists():
                return JsonResponse({'error': 'Ya existe otro usuario con ese email'}, status=400)
            user.email = new_email

        if 'role_id' in data:
            rid = data.get('role_id')
            if rid in (None, '', 0):
                user.role = None
            else:
                role = Role.objects.filter(id=rid).first()
                if not role:
                    return JsonResponse({'error': 'Rol no encontrado'}, status=400)
                # Si quitamos el rol admin a un admin, asegurarnos de no dejar el sistema sin admins
                if (user.role and _is_admin(user) and role.role_name.lower()
                        not in ('admin', 'administrator', 'administrador')):
                    if not User.objects.filter(role__role_name__iexact='admin', is_active=True).exclude(id=user.id).exists():
                        return JsonResponse({'error': 'Debe quedar al menos un administrador activo'}, status=400)
                user.role = role

        if 'group_id' in data:
            gid = data.get('group_id')
            if gid in (None, '', 0):
                user.group = None
            else:
                group = Group.objects.filter(id=gid).first()
                if not group:
                    return JsonResponse({'error': 'Grupo no encontrado'}, status=400)
                user.group = group

        if 'is_active' in data:
            user.is_active = bool(data['is_active'])

        user.save()
        return JsonResponse({'ok': True, 'user': _admin_serialize_user(user)})

    if request.method == 'DELETE':
        try:
            user_id = int(request.GET.get('id') or 0)
        except (TypeError, ValueError):
            return JsonResponse({'error': 'id requerido'}, status=400)
        if not user_id:
            return JsonResponse({'error': 'id requerido'}, status=400)
        if user_id == request.user.id:
            return JsonResponse({'error': 'No puedes eliminar tu propia cuenta'}, status=400)
        user = User.objects.filter(id=user_id).first()
        if not user:
            return JsonResponse({'error': 'Usuario no encontrado'}, status=404)

        # Si tiene tickets relacionados, mejor desactivar que borrar para preservar FK
        has_tickets = (
            Ticket.objects.filter(Q(requester=user) | Q(assignee=user) | Q(created_by=user)).exists()
            or Comment.objects.filter(user=user).exists()
        )
        if has_tickets:
            user.is_active = False
            user.save(update_fields=['is_active'])
            return JsonResponse({'ok': True, 'deactivated': True})
        user.delete()
        return JsonResponse({'ok': True, 'deleted': True})

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
    _PROTECTED_ROLES = {'admin', 'administrator', 'administrador', 'end user', 'end-user'}

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


def _serialize_assignment_rule(rule):
    return {
        'id': rule.id,
        'name': rule.name,
        'active': rule.active,
        'group_id': rule.group_id,
        'service': rule.service or '',
        'channel': rule.channel or '',
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
        rules = AssignmentRule.objects.prefetch_related('members__user').order_by('name')
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
        rule.group_id = _as_int_or_none(data.get('group_id'))
        rule.service = (data.get('service') or '').strip() or None
        rule.channel = (data.get('channel') or '').strip() or None
        rule.save()

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
        'assigned_group_id': policy.assigned_group_id,
        'first_response_minutes': policy.first_response_minutes,
        'resolution_minutes': policy.resolution_minutes,
    }


@_admin_required
def admin_sla_policies_api(request):
    if request.method == 'GET':
        policies = SLAPolicy.objects.select_related('assigned_group').order_by('name')
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
