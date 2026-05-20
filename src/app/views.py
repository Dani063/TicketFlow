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

_CUSTOMER_SORT_FIELDS = {
    'id':         'id',
    'name':       'name',
    'email':      'email',
    'status':     'group__group_name',
    'role':       'role__role_name',
    'group':      'group__group_name',
    'created_at': 'created_at',
}

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
        base_qs = Ticket.objects.filter(merged_into__isnull=True).order_by('-updated_at')
    else:
        base_qs = Ticket.objects.filter(
            merged_into__isnull=True
        ).filter(
            Q(requester=request.user) | Q(ccs=request.user)
        ).distinct().order_by('-updated_at')

    # Los contadores son costosos (25 COUNTs). Solo se calculan cuando counts=1.
    filtros = {}
    if compute_counts:
        filtros = {
            "telefonica_mes": base_qs.filter(service="Telefonica", created_at__gte=timezone.now()-timedelta(days=30)).count(),
            "unsolved_no_tareas": base_qs.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea")).count(),
            "unassigned": base_qs.filter(assignee__isnull=True).count(),
            "all_unsolved_no_tareas": base_qs.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea")).count(),
            "recently_updated": base_qs.count(),
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
            "sus_no_cerrados": base_qs.filter(requester=request.user).exclude(status="closed").count(),
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

    if view == "telefonica_mes":
        tickets = base_qs.filter(service="Telefonica", created_at__gte=timezone.now()-timedelta(days=30))
    elif view == "unsolved_no_tareas":
        tickets = base_qs.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea"))
    elif view == "unassigned":
        tickets = base_qs.filter(assignee__isnull=True)
    elif view == "all_unsolved_no_tareas":
        tickets = base_qs.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea"))
    elif view == "recently_updated":
        tickets = base_qs
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
        tickets = base_qs.filter(requester=request.user).exclude(status="closed")
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
    if db_sort:
        tickets = tickets.order_by(f'-{db_sort}' if sort_dir == 'desc' else db_sort)

    total = tickets.count()
    total_pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, total_pages)
    offset = (page - 1) * page_size

    page_qs = tickets.select_related('requester', 'assignee')[offset:offset + page_size]

    data = [
        {
            "id": t.id,
            "subject": t.subject,
            "requester": t.requester.name if t.requester else "-",
            "updated_at": t.updated_at.strftime("%d/%m/%Y %H:%M"),
            "service": t.service or "-",
            "assignee": t.assignee.name if t.assignee else "-",
            "status": t.status,
        }
        for t in page_qs
    ]
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

    tickets = Ticket.objects.filter(
    Q(requester__id=customer.id) | Q(assignee__id=customer.id) | Q(ccs__id=customer.id) | Q(created_by_id=customer.id)
    ).distinct().order_by("-updated_at")

    return render(request, "tickets/customer_profile.html", {
        "customer": customer,
        "tickets": tickets
    })

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
    tickets = Ticket.objects.filter(
    Q(requester__id=user.id) | Q(assignee__id=user.id) | Q(ccs__id=user.id) | Q(created_by_id=user.id)
    ).distinct().order_by("-updated_at")

    return render(request, "tickets/profile.html", {
        "user": user,
        "tickets": tickets
    })

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

    comment = Comment.objects.create(
        ticket=ticket,
        user=request.user,
        content=content,
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
        'created_at': comment.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'ticket_id': ticket.id,
        'user_id': request.user.id,
        'username': request.user.name,
        'is_public': comment.is_public,
        'attachments': list(atts),
        'new_status': applied_status,  # ← devolver el estado final
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
    if _is_agent(request.user):
        tickets = (
            Ticket.objects
            .filter(Q(assignee=request.user) | Q(created_by=request.user))
            .select_related('requester', 'assignee')
            .order_by('-updated_at')
            .distinct()[:10]
        )
    else:
        tickets = (
            Ticket.objects
            .filter(Q(requester=request.user) | Q(ccs=request.user))
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

    # --- Tickets ---
    ticket_qs = Ticket.objects.filter(merged_into__isnull=True) if _is_agent(request.user) else \
        Ticket.objects.filter(merged_into__isnull=True).filter(
            Q(requester=request.user) | Q(ccs=request.user)
        ).distinct()

    tickets = ticket_qs.filter(
        Q(subject__icontains=q) |
        Q(description__icontains=q) |
        Q(service__icontains=q) |
        Q(status__icontains=q) |
        Q(priority__icontains=q) |
        Q(requester__name__icontains=q) |
        Q(requester__email__icontains=q) |
        Q(assignee__name__icontains=q) |
        Q(assignee__email__icontains=q) |
        Q(tags__name__icontains=q)
    ).distinct()[:10]

    tickets_data = [{
        "id": t.id,
        "subject": t.subject,
        "status": t.status,
        "requester": t.requester.name if t.requester else "-",
        "assignee": t.assignee.name if t.assignee else "-",
        "service": t.service or "-"
    } for t in tickets]

    # --- Usuarios (solo agentes/admins) ---
    if _is_agent(request.user):
        users = User.objects.filter(
            Q(name__icontains=q) |
            Q(email__icontains=q) |
            Q(role__role_name__icontains=q) |
            Q(group__group_name__icontains=q)
        ).distinct()[:10]
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
