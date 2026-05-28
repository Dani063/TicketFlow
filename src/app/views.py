"""
Definition of views.
"""
# -*- coding: utf-8 -*-
from django.db.models import Q, OuterRef, Subquery, Count
from django.utils.dateparse import parse_datetime
from datetime import datetime, timedelta
from types import new_class
from unicodedata import category
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from .models import Brand, Organization, Ticket, Comment, User, TicketTag, Attachment, TicketEvent, Notification, Role, Group, Macro, SatisfactionRating
from .forms import TicketForm, CommentForm, UserForm
from django.contrib.auth.hashers import check_password
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login as auth_login
from django.contrib.auth import logout
from django.views.decorators.csrf import csrf_exempt
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

logger = logging.getLogger('app.views')

_DEFAULT_PAGE_SIZE = 50
_ALLOWED_PAGE_SIZES = {10, 20, 50, 100, 150}

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
    if not html:
        return ''
    # Safety net: if the input arrives entity-encoded (e.g. some clients
    # over-escape) decode once so bleach sees real tags rather than text.
    import html as _htmllib
    if '<' not in html and '&lt;' in html:
        html = _htmllib.unescape(html)
    if not _HAS_BLEACH:
        return _htmllib.escape(html)
    cleaned = bleach.clean(
        html,
        tags=_ALLOWED_HTML_TAGS,
        attributes=_ALLOWED_HTML_ATTRS,
        protocols=_ALLOWED_HTML_PROTOCOLS,
        strip=True,
    )
    # Safety net: remove Quill-specific helper spans/elements that may persist
    # (e.g. <span class="ql-ui">, <span class="ql-cursor">) and any empty spans
    import re
    cleaned = re.sub(r'<span\s+class="ql-[^"]*"\s*(?:data-[^\s]*="[^"]*"\s*)*(?:contenteditable="[^"]*"\s*)*>\s*</span>', '', cleaned)
    cleaned = re.sub(r'<span\s*>\s*</span>', '', cleaned)
    return cleaned


def _attach_last_comments(ticket_list):
    if not ticket_list:
        return
    ids = [t.id for t in ticket_list]
    lc_map = {}
    for c in Comment.objects.filter(ticket_id__in=ids).select_related('user').order_by('ticket_id', '-created_at'):
        if c.ticket_id not in lc_map:
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

    solventado = tickets_qs.filter(status__in=["closed", "resolved"]).count()

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

    # Global satisfaction stats (all Zendesk-imported ratings)
    bien = SatisfactionRating.objects.filter(score='good').count()
    mal  = SatisfactionRating.objects.filter(score='bad').count()
    total_rated = bien + mal
    satisfaccion_pct = round((bien / total_rated) * 100) if total_rated > 0 else None

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

@login_required
def filter_tickets(request):
    """
    Devuelve tickets paginados en JSON según el 'view' seleccionado.
    Parámetros: view, page (1-based), counts=1 (opcional, activa el cálculo de contadores).
    """
    view = request.GET.get("view")
    compute_counts = request.GET.get("counts", "0") == "1"
    sort_by  = request.GET.get("sort_by", "")
    sort_dir = request.GET.get("sort_dir", "asc") if request.GET.get("sort_dir") in ("asc", "desc") else "asc"
    try:
        page = max(1, int(request.GET.get("page", 1) or 1))
    except (ValueError, TypeError):
        page = 1
    page_size = _parse_page_size(request.GET.get("page_size"))

    if _is_agent(request.user):
        base_qs = Ticket.objects.filter(merged_into__isnull=True, is_deleted=False).order_by('-updated_at')
    else:
        base_qs = Ticket.objects.filter(
            merged_into__isnull=True, is_deleted=False
        ).filter(
            Q(requester=request.user) | Q(ccs=request.user)
        ).distinct().order_by('-updated_at')

    # Los contadores son costosos (25 COUNTs). Solo se calculan cuando counts=1.
    filtros = {}
    if compute_counts:
        filtros = {
            "mis_tickets": base_qs.count(),
            "telefonica_mes": base_qs.filter(service="Telefonica", created_at__gte=timezone.now()-timedelta(days=30)).count(),
            "unsolved_no_tareas": base_qs.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea")).count(),
            "unassigned": base_qs.filter(assignee__isnull=True).count(),
            "all_unsolved_no_tareas": base_qs.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea")).count(),
            "recently_updated": base_qs.filter(updated_at__gte=timezone.now()-timedelta(hours=24)).count(),
            "recently_solved": base_qs.filter(status="resolved").count(),
            "pendientes": base_qs.filter(status="pending").count(),
            "tareas": base_qs.filter(type="tarea").count(),
            "unsolved_groups": base_qs.filter(~Q(status__in=["closed", "resolved"]), assignee__group=request.user.group).count(),
            "rated_last7": 0,
            "internos_comuny": base_qs.filter(service="Comunycarse", status="open").count(),
            "abiertos_ecomfax": base_qs.filter(service="ecomfax", status="open").count(),
            "recordia_sgsd": base_qs.filter(service="Recordia SGSD", status="open").count(),
            "closed": base_qs.filter(status="closed").count(),
            "sus_pendientes": base_qs.filter(requester=request.user, status="pending").count(),
            "espera": base_qs.filter(status="espera").count() if hasattr(Ticket, "espera") else 0,
            "abiertos": base_qs.filter(status="open").count(),
            "sus_no_cerrados": (base_qs.exclude(status="closed") if not _is_agent(request.user) else base_qs.filter(requester=request.user).exclude(status="closed")).count(),
            "ultimos_cerrados": base_qs.filter(status="closed").count(),
            "no_resueltos": base_qs.exclude(status="resolved").count(),
            "twitter": base_qs.filter(channel="twitter").count(),
            "twitter_dm": base_qs.filter(channel="twitter_dm").count(),
            "twitter_like": base_qs.filter(channel="twitter_like").count(),
            "sus_tareas": base_qs.filter(requester=request.user, type="tarea").count(),
            "resueltos": base_qs.filter(status="resolved").count(),
            "new_in_groups": base_qs.filter(assignee__group=request.user.group, created_at__gte=timezone.now()-timedelta(days=7)).count(),
            "open": base_qs.filter(status="open").count(),
            "no_update_48h": base_qs.filter(updated_at__lte=timezone.now()-timedelta(hours=48)).count(),
        }

    if view == "mis_tickets":
        # Vista de end user: TODOS sus tickets (requester o cc), incluidos cerrados/resueltos.
        # base_qs ya está filtrado por requester/cc para end users; agentes ven todo.
        tickets = base_qs
    elif view == "telefonica_mes":
        tickets = base_qs.filter(service="Telefonica", created_at__gte=timezone.now()-timedelta(days=30))
    elif view == "unsolved_no_tareas":
        tickets = base_qs.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea"))
    elif view == "unassigned":
        tickets = base_qs.filter(assignee__isnull=True)
    elif view == "all_unsolved_no_tareas":
        tickets = base_qs.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea"))
    elif view == "recently_updated":
        tickets = base_qs.filter(updated_at__gte=timezone.now()-timedelta(hours=24))
    elif view == "recently_solved":
        tickets = base_qs.filter(status="resolved")
    elif view == "pendientes":
        tickets = base_qs.filter(status="pending")
    elif view == "tareas":
        tickets = base_qs.filter(type="tarea")
    elif view == "unsolved_groups":
        tickets = base_qs.filter(~Q(status__in=["closed", "resolved"]), assignee__group=request.user.group)
    elif view == "rated_last7":
        since = timezone.now() - timedelta(days=7)
        rated_ids = SatisfactionRating.objects.filter(
            created_at__gte=since, score__in=['good', 'bad']
        ).values_list('ticket_id', flat=True)
        tickets = base_qs.filter(id__in=rated_ids)
    elif view == "internos_comuny":
        tickets = base_qs.filter(service="Comunycarse", status="open")
    elif view == "abiertos_ecomfax":
        tickets = base_qs.filter(service="ecomfax", status="open")
    elif view == "recordia_sgsd":
        tickets = base_qs.filter(service="Recordia SGSD", status="open")
    elif view == "closed":
        tickets = base_qs.filter(status="closed")
    elif view == "sus_pendientes":
        tickets = base_qs.filter(requester=request.user, status="pending")
    elif view == "espera":
        tickets = base_qs.filter(status="espera") if hasattr(Ticket, "espera") else base_qs.none()
    elif view == "abiertos":
        tickets = base_qs.filter(status="open")
    elif view == "sus_no_cerrados":
        # Para end users: todos sus tickets (requester o ccs) sin cerrados
        # Para agentes: solo tickets donde son requester, sin cerrados
        if _is_agent(request.user):
            tickets = base_qs.filter(requester=request.user).exclude(status="closed")
        else:
            tickets = base_qs.exclude(status="closed")
    elif view == "ultimos_cerrados":
        tickets = base_qs.filter(status="closed")
    elif view == "no_resueltos":
        tickets = base_qs.exclude(status="resolved")
    elif view == "twitter":
        tickets = base_qs.filter(channel="twitter")
    elif view == "twitter_dm":
        tickets = base_qs.filter(channel="twitter_dm")
    elif view == "twitter_like":
        tickets = base_qs.filter(channel="twitter_like")
    elif view == "sus_tareas":
        tickets = base_qs.filter(requester=request.user, type="tarea")
    elif view == "resueltos":
        tickets = base_qs.filter(status="resolved")
    elif view == "new_in_groups":
        tickets = base_qs.filter(assignee__group=request.user.group, created_at__gte=timezone.now()-timedelta(days=7))
    elif view == "open":
        tickets = base_qs.filter(status="open")
    elif view == "no_update_48h":
        tickets = base_qs.filter(updated_at__lte=timezone.now()-timedelta(hours=48))
    else:
        tickets = base_qs

    db_sort = _TICKET_SORT_FIELDS.get(sort_by)
    db_sort_signed = (f'-{db_sort}' if sort_dir == 'desc' else db_sort) if db_sort else None

    # Si el usuario ha pedido ordenar por una columna, desactivamos la agrupación
    # para mostrar la tabla plana ordenada por su criterio.
    if db_sort_signed:
        group_by = None
        group_sort = None
    else:
        group_by = _TICKET_GROUP_BY.get(view)
        group_sort = _GROUP_DB_SORT.get(group_by) if group_by else None

    if group_sort:
        tickets = tickets.order_by(group_sort, '-updated_at')
    elif db_sort_signed:
        tickets = tickets.order_by(db_sort_signed)

    total = tickets.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    offset = (page - 1) * page_size

    tickets_page = list(tickets.select_related('requester', 'assignee', 'assigned_group')[offset:offset + page_size])

    last_comments = {}
    if tickets_page:
        ticket_ids = [t.id for t in tickets_page]
        for c in (Comment.objects
                  .filter(ticket_id__in=ticket_ids)
                  .select_related('user')
                  .order_by('ticket_id', '-created_at')):
            if c.ticket_id not in last_comments:
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
        },
        "sort": {"sort_by": sort_by if db_sort else "", "sort_dir": sort_dir},
        "group_by": group_by,
    })

@login_required
def filter_customers(request):
    if not _is_agent(request.user) and not _is_admin(request.user):
         return JsonResponse({"error": "No permission"}, status=403)

    view = request.GET.get("view")
    sort_by  = request.GET.get("sort_by", "")
    sort_dir = request.GET.get("sort_dir", "asc") if request.GET.get("sort_dir") in ("asc", "desc") else "asc"
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

@login_required
def tags_api(request):
    q = request.GET.get('q', '')
    tags = TicketTag.objects.filter(name__icontains=q) if q else TicketTag.objects.all()
    results = [{"id": tag.id, "text": tag.name} for tag in tags]
    return JsonResponse({"results": results})

@login_required
def customers_list(request):
    if not _is_agent(request.user) and not _is_admin(request.user):
         return render(request, "tickets/403.html", {"error": "No tienes permisos para ver clientes."}, status=403)

    context = {
        'username': request.user.name,
        'email': request.user.email,
        'is_admin': _is_admin(request.user),
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

def sso_callback(request):
    """Vista que carga el frontend para procesar el token SSO."""
    return render(request, "tickets/sso_callback.html", {
        "SSO_LOGIN_API_URL": getattr(DJANGO_SETTINGS, 'SSO_LOGIN_API_URL', 'https://login-api.agentia365.com')
    })

@csrf_exempt
@require_POST
def sso_complete(request):
    """
    Recibe la identidad extraída por el frontend desde Authentication/Me.
    Verifica o crea el usuario local y genera la sesión de Django.
    """
    try:
        data = json.loads(request.body)
        email = data.get('email')
        name = data.get('name', 'Usuario SSO')
        # claims = data.get('claims', []) # Aquí puedes leer claims si los envías

        if not email:
            return JsonResponse({'error': 'Email es requerido para completar el SSO'}, status=400)

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

        return JsonResponse({'status': 'ok', 'message': 'Sesión completada exitosamente'})
    except Exception as e:
        logger.error(f"Error en sso_complete: {str(e)}")
        return JsonResponse({'error': str(e)}, status=500)

@csrf_exempt
def register(request):
    if request.method == 'POST':
        data = json.loads(request.body)
        email = data.get('email')
        name = data.get('name')
        password = data.get('password')
        if User.objects.filter(email=email).exists():
            return JsonResponse({'error': 'El usuario ya existe'}, status=400)
        user = User(email=email, name=name)
        user.set_password(password)  # Hashear la contrase�a
        user.save()
        return JsonResponse({'message': 'Usuario registrado correctamente'})

@csrf_exempt
def user_login(request):
    if request.method == 'POST':
        try:
            data = json.loads(request.body)
            email = data.get('email')
            password = data.get('password')

            # Autenticar al usuario con el backend personalizado
            user = authenticate(request, username=email, password=password)

            if user is not None:
                auth_login(request, user, backend='app.backends.EmailBackend')  # Forzar el backend correcto
                return JsonResponse({'message': 'Inicio de sesión exitoso'})
            else:
                return JsonResponse({'error': 'Credenciales inválidas'}, status=400)
        except Exception as e:
            return JsonResponse({'error': str(e)}, status=500)
    return JsonResponse({'detail': 'Method not allowed'}, status=405)

def user_logout(request):
    logout(request)  # Cierra la sesión del usuario
    return redirect('login') # Redirige al login
   
def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    return render(request, 'tickets/ticket_detail.html', {'ticket': ticket})


def ticket_detail_api(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    if not _is_agent(request.user):
        is_involved = (
            ticket.requester_id == request.user.id or
            ticket.created_by_id == request.user.id or
            ticket.ccs.filter(id=request.user.id).exists()
        )
        if not is_involved:
            return JsonResponse({"error": "No tienes acceso a este ticket."}, status=403)

    # Marcar como leídas las notificaciones del usuario para este ticket
    Notification.objects.filter(user=request.user, ticket=ticket, read=False).update(read=True)

    data = {
        'empresa': ticket.brand.name if ticket.brand else '',
        'solicitante': ticket.requester_id,
        'asignado': ticket.assignee_id,
        'grupo': ticket.assigned_group_id,
        'ccs': [user.id for user in ticket.ccs.all()],
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
        'subject': ticket.subject,
        'content': ticket.description,
    }
    return JsonResponse(data)


def _is_agent(user):
    role_name = (getattr(getattr(user, 'role', None), 'role_name', '') or '').lower()
    # tratamos "End user" como cliente; todo lo demás se considera agente/staff
    return role_name not in ('end user', 'end-user', 'cliente', 'customer')

def _is_admin(user):
    role_name = (getattr(getattr(user, 'role', None), 'role_name', '') or '').lower()
    return role_name in ('admin', 'administrator', 'administrador')

@login_required
def create_ticket(request):
    id_param = request.GET.get('id', None)

    if request.method == 'POST':
        # --- Leer y sanear inputs (evita NULL en campos obligatorios) ---
        subject = (request.POST.get('subject') or '').strip()
        content = ((request.POST.get('content') or request.POST.get('message')) or '').strip()
        status  = (request.POST.get('status')  or 'open').strip().lower()
        priority = (request.POST.get('prioridad') or '').strip().lower()

        # Mapear placeholders o valores no válidos a defaults
        if status not in ('open', 'pending', 'closed', 'resolved'):
            status = 'open'
        if priority not in ('low', 'normal', 'high', 'urgent'):
            priority = None
        if not subject:
            subject = '(sin asunto)'          # nunca NULL en DB
        # description en modelo es not null: mínimo cadena vacía
        description = content or ''

        empresa_name = (request.POST.get('empresa') or '').strip() or None
        brand_obj    = Brand.objects.get_or_create(name=empresa_name)[0] if empresa_name else None
        language  = request.POST.get('idioma') or None
        category  = request.POST.get('categoria') or None
        channel   = request.POST.get('canal') or None
        service   = request.POST.get('servicio') or None
        tipo      = request.POST.get('tipo') or None

        solicitante_id = request.POST.get('solicitante') or request.user.id
        asignado_id    = request.POST.get('asignado')
        grupo_id       = request.POST.get('grupo') or None

        due_at_raw = (request.POST.get('due_at') or '').strip()
        due_at = parse_datetime(due_at_raw.replace('T', ' ')) if due_at_raw else None

        security_related = request.POST.get('security_related') in ('1', 'true', 'on')
        monitoring       = request.POST.get('monitoring')       in ('1', 'true', 'on')
        approval_status  = (request.POST.get('approval_status') or '').strip() or None
        resolution_type  = (request.POST.get('resolution_type') or '').strip() or None
        required_tasks   = (request.POST.get('required_tasks')  or '').strip() or None
        if not asignado_id:
            agentes = User.objects.filter(group_id='2')
            asignado_id = random.choice(list(agentes)).id if agentes.exists() else None

        ccs_ids = request.POST.getlist('ccs')
        tags_in = request.POST.getlist('tags')

        def _set_m2m(ticket):
            if ccs_ids:
                ticket.ccs.set(ccs_ids)
            ticket.tags.clear()
            for tag in tags_in:
                if tag.isdigit():
                    try:
                        ticket.tags.add(TicketTag.objects.get(id=tag))
                    except TicketTag.DoesNotExist:
                        pass
                else:
                    tag_obj, _ = TicketTag.objects.get_or_create(name=tag)
                    ticket.tags.add(tag_obj)

        # --- Actualizar ticket existente (id numérico) ---
        if id_param and id_param.isdigit():
            ticket = get_object_or_404(Ticket, id=int(id_param))

            # Capture original values for change tracking
            _orig_status     = ticket.status
            _orig_priority   = ticket.priority
            _orig_subject    = ticket.subject
            _orig_assignee   = ticket.assignee_id
            _orig_group      = ticket.assigned_group_id

            ticket.subject     = subject
            ticket.description = description
            ticket.assignee_id = asignado_id
            ticket.brand       = brand_obj
            ticket.type        = tipo
            ticket.channel     = channel
            ticket.service     = service
            ticket.language    = language
            ticket.category    = category
            ticket.priority    = priority
            ticket.status      = status
            ticket.assigned_group_id = grupo_id if grupo_id else None
            ticket.due_at = due_at
            ticket.security_related = security_related
            ticket.monitoring = monitoring
            ticket.approval_status = approval_status
            ticket.resolution_type = resolution_type
            ticket.required_tasks = required_tasks
            ticket.save()
            _set_m2m(ticket)

            # Record field changes as TicketEvents
            _now = timezone.now()
            _str = lambda v: str(v) if v is not None else None
            for _fname, _old, _new in [
                ('status',      _orig_status,          ticket.status),
                ('priority',    _orig_priority,         ticket.priority),
                ('subject',     _orig_subject,          ticket.subject),
                ('assignee_id', _str(_orig_assignee),   _str(ticket.assignee_id)),
                ('group_id',    _str(_orig_group),      _str(ticket.assigned_group_id)),
            ]:
                if _old != _new:
                    TicketEvent.objects.create(
                        ticket=ticket, actor=request.user,
                        field_name=_fname, old_value=_old, new_value=_new,
                        created_at=_now,
                    )

            _notify_users(ticket, f"Ticket #{ticket.id} actualizado", actor=request.user)
            if content:
                requested_public = str((request.POST.get('is_public') or 'true')).lower() in ('true','1','yes','on')
                final_is_public = requested_public if _is_agent(request.user) else True

                Comment.objects.create(
                    ticket_id=ticket.id,
                    user_id=request.user.id,
                    content=content,
                    created_at=timezone.now(),
                    is_public=final_is_public,
                )
            return redirect(f'{request.path}?id={ticket.id}')

        # --- Crear ticket nuevo (id temporal o sin id) ---
        ticket = Ticket.objects.create(
            subject=subject,
            description=description,
            status=status,
            priority=priority,
            requester_id=solicitante_id,
            assignee_id=asignado_id,
            assigned_group_id=grupo_id if grupo_id else None,
            created_by_id=request.user.id,
            brand=brand_obj,
            type=tipo,
            channel=channel,
            service=service,
            language=language,
            created_at=timezone.now(),
            category=category,
            due_at=due_at,
            security_related=security_related,
            monitoring=monitoring,
            approval_status=approval_status,
            resolution_type=resolution_type,
            required_tasks=required_tasks,
        )
        _set_m2m(ticket)
        _notify_users(ticket, f"Nuevo ticket #{ticket.id}: {ticket.subject}", actor=request.user)
        if content:
            requested_public = str((request.POST.get('is_public') or 'true')).lower() in ('true','1','yes','on')
            final_is_public = requested_public if _is_agent(request.user) else True

            Comment.objects.create(
                ticket_id=ticket.id,
                user_id=request.user.id,
                content=content,
                created_at=timezone.now(),
                is_public=final_is_public,
            )
        return redirect(f'{request.path}?id={ticket.id}')

    # GET .
    # Gate: end users only see tickets they are involved in (requester / cc / created_by).
    # Otherwise their tab opens and renders, but the JS XHR /api/tickets/<id>/ fails with
    # 403 and form fields stay empty — which the user perceives as "ticket viewing error".
    if id_param and id_param.isdigit():
        _ticket_check = get_object_or_404(Ticket, id=int(id_param))
        if not _is_agent(request.user):
            _involved = (
                _ticket_check.requester_id == request.user.id or
                _ticket_check.created_by_id == request.user.id or
                _ticket_check.ccs.filter(id=request.user.id).exists()
            )
            if not _involved:
                return render(request, "tickets/403.html",
                              {"error": "No tienes acceso a este ticket."}, status=403)

    usuarios = User.objects.filter(is_active=True).only('id', 'name', 'email').order_by('name')
    agentes = User.objects.filter(group=2).only('id', 'name', 'email').order_by('name')
    tags = TicketTag.objects.only('id', 'name').all()
    todos = User.objects.only('id', 'name', 'email').order_by('name')
    grupos = Group.objects.all().order_by('group_name')

    # Marcas: combinamos las hardcoded con las que ya existen en BD (p.ej. importadas de Zendesk)
    hardcoded_brands = [
        "Audio Simple Notification Service",
        "Comunycarse Helpdesk",
        "EcomFax",
        "Recordia",
    ]
    db_brands = list(Brand.objects.values_list('name', flat=True))
    empresas = sorted(set(hardcoded_brands + db_brands))

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

@login_required
@require_POST
def take_ticket(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
    if ticket.assignee_id == request.user.id:
        return JsonResponse({'success': False, 'error': 'already_assigned'})
    prev = str(ticket.assignee_id) if ticket.assignee_id else None
    ticket.assignee = request.user
    ticket.save(update_fields=['assignee'])
    TicketEvent.objects.create(
        ticket=ticket, actor=request.user,
        field_name='assignee_id',
        old_value=prev,
        new_value=str(request.user.id),
    )
    _notify_users(ticket, f"Ticket #{ticket.id} asignado a {request.user.name}", actor=request.user)
    return JsonResponse({'success': True, 'assignee_id': request.user.id, 'assignee_name': request.user.name})


# Funcionalidades de Comentarios

@login_required
@require_POST
def add_comment(request, ticket_id):
    data = json.loads(request.body)
    content = (data.get('content') or '').strip()
    if not content:
        return JsonResponse({'error': 'El contenido no puede estar vacío.'}, status=400)

    ticket = get_object_or_404(Ticket, id=ticket_id)

    requested_public = bool(data.get('is_public', True))
    final_is_public = requested_public if _is_agent(request.user) else True  # clientes → siempre público

    html_body = (data.get('html_body') or '').strip()
    if html_body:
        html_body = _sanitize_email_html(html_body)

    comment = Comment.objects.create(
        ticket=ticket,
        user=request.user,
        content=content,
        html_body=html_body or None,
        created_at=timezone.now(),
        is_public=final_is_public,
    )

    # --- NUEVO: cambio de estado inline ---
    new_status = (data.get('new_status') or '').lower()
    allowed = {'open', 'pending', 'resolved', 'closed'}
    applied_status = ticket.status
    if new_status in allowed and new_status != ticket.status:
        prev = ticket.status
        ticket.status = new_status
        if new_status == 'closed':
            ticket.closed_at = timezone.now()
        ticket.updated_at = timezone.now()
        ticket.save(update_fields=['status', 'updated_at', 'closed_at'])
        TicketEvent.objects.create(
            ticket=ticket,
            actor=request.user,
            field_name='status',
            old_value=prev,
            new_value=new_status,
            created_at=timezone.now(),
        )
        applied_status = new_status

    # Vincular adjuntos pendientes al comentario
    attachment_ids = data.get('attachment_ids') or []
    if attachment_ids:
        Attachment.objects.filter(
            id__in=attachment_ids,
            ticket=ticket,
            comment__isnull=True
        ).update(comment=comment)

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
@login_required
@login_required
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


def notifications_api(request):
    notifs = Notification.objects.filter(user=request.user, read=False).order_by("-created_at")[:20]
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

@login_required
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
    seen = set()
    targets = []
    for u in [ticket.requester, ticket.assignee]:
        if u and u != actor and u.id not in seen:
            seen.add(u.id)
            targets.append(u)

    for u in targets:
        Notification.objects.create(
            user=u,
            ticket=ticket,
            message=message
        )

@login_required
@require_POST
def mark_notification_read(request, notif_id):
    notif = get_object_or_404(Notification, id=notif_id, user=request.user)
    notif.read = True
    notif.save()
    return JsonResponse({"ok": True})

@login_required
@require_POST
def upload_attachment(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
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

@login_required
@require_GET
def macros_api(request):
    if not _is_agent(request.user):
        return JsonResponse({'error': 'Forbidden'}, status=403)
    macros = Macro.objects.filter(active=True).order_by('name').values('id', 'name', 'description', 'actions')
    return JsonResponse({'macros': list(macros)})


@login_required
@require_GET
def global_search(request):
    q = (request.GET.get("q") or "").strip()
    if not q:
        return JsonResponse({"tickets": [], "users": []})

    ticket_qs = (
        Ticket.objects.filter(merged_into__isnull=True, is_deleted=False)
        if _is_agent(request.user) else
        Ticket.objects.filter(merged_into__isnull=True, is_deleted=False)
        .filter(Q(requester=request.user) | Q(ccs=request.user))
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


@login_required
@require_POST
def merge_ticket(request, ticket_id):
    if not _is_agent(request.user):
        return JsonResponse({'error': 'Permiso denegado'}, status=403)

    ticket = get_object_or_404(Ticket, pk=ticket_id)

    if ticket.merged_into_id:
        return JsonResponse({'error': 'Este ticket ya está fusionado'}, status=400)

    try:
        target_id = int(request.POST.get('target_ticket_id', ''))
        target = Ticket.objects.get(pk=target_id)
    except (ValueError, TypeError, Ticket.DoesNotExist):
        return JsonResponse({'error': 'Ticket destino no encontrado'}, status=404)

    if target.id == ticket.id:
        return JsonResponse({'error': 'No puedes fusionar un ticket consigo mismo'}, status=400)

    if target.merged_into_id:
        return JsonResponse({'error': 'El ticket destino también está fusionado'}, status=400)

    Comment.objects.filter(ticket=ticket).update(ticket=target)
    TicketEvent.objects.filter(ticket=ticket).update(ticket=target)

    ticket.merged_into = target
    ticket.status = 'closed'
    ticket.save()

    TicketEvent.objects.create(
        ticket=target,
        actor=request.user,
        field_name='merge',
        old_value=None,
        new_value=f'#{ticket.zendesk_id or ticket.id}',
        created_at=timezone.now(),
    )

    _notify_users(target, f'Ticket #{ticket.zendesk_id or ticket.id} fusionado aquí', actor=request.user)

    return JsonResponse({'ok': True, 'target_id': target.id})


@login_required
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


@login_required
@require_POST
def bulk_merge(request):
    if not _is_agent(request.user):
        return JsonResponse({'error': 'Permiso denegado'}, status=403)
    try:
        data = json.loads(request.body)
        ids = data.get('ids', [])
        target_id = int(data.get('target_id', 0))
    except (json.JSONDecodeError, AttributeError, ValueError, TypeError):
        return JsonResponse({'error': 'Payload inválido'}, status=400)
    if not ids or not target_id:
        return JsonResponse({'error': 'ids y target_id requeridos'}, status=400)

    try:
        target = Ticket.objects.get(pk=target_id, merged_into__isnull=True, is_deleted=False)
    except Ticket.DoesNotExist:
        return JsonResponse({'error': 'Ticket destino no encontrado'}, status=404)

    tickets_to_merge = Ticket.objects.filter(
        id__in=ids, merged_into__isnull=True, is_deleted=False
    ).exclude(id=target_id)

    merged_count = 0
    for ticket in tickets_to_merge:
        Comment.objects.filter(ticket=ticket).update(ticket=target)
        TicketEvent.objects.filter(ticket=ticket).update(ticket=target)
        ticket.merged_into = target
        ticket.status = 'closed'
        ticket.save()
        TicketEvent.objects.create(
            ticket=target,
            actor=request.user,
            field_name='merge',
            old_value=None,
            new_value=f'#{ticket.zendesk_id or ticket.id}',
            created_at=timezone.now(),
        )
        merged_count += 1

    _notify_users(target, f'{merged_count} ticket(s) fusionados aquí', actor=request.user)


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
    """Decorator: 403 si no es admin; redirige a login si no autenticado."""
    from functools import wraps

    @wraps(view_func)
    def _wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect('login')
        if not _is_admin(request.user):
            # Para vistas API devolver JSON; para vistas HTML, renderizar 403
            if request.path.startswith('/api/'):
                return JsonResponse({'error': 'Forbidden'}, status=403)
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
@csrf_exempt
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
@csrf_exempt
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
@csrf_exempt
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
@csrf_exempt
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
