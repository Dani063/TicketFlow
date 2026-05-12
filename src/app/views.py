"""
Definition of views.
"""
# -*- coding: utf-8 -*-
from django.db.models import Q
from datetime import datetime, timedelta
from types import new_class
from unicodedata import category
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from .models import Ticket, Comment, User, TicketTag, Attachment, TicketHistory, Notification, Role
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


@login_required
def home(request):
    user = request.user

    # Tickets asociados al usuario
    tickets = Ticket.objects.filter(
        Q(requester=user) | Q(assignee=user) | Q(ccs=user)
    ).distinct()

    # --- Sección "Tickets Abiertos" ---
    abiertos_you = tickets.filter(status="open").count()
    abiertos_groups = 0
    if user.group:  # si pertenece a un grupo
        abiertos_groups = Ticket.objects.filter(
            assignee__group=user.group, status="open"
        ).distinct().count()

    # --- Sección "Estadísticas de Tickets" ---
    bien = "-"        # aún no implementado
    mal = "-"         # aún no implementado
    solventado = tickets.filter(status="closed").count()

    context = {
        "username": user.name,
        "email": user.email,
        "tickets": tickets,
        "my_tickets": tickets,
        "stats": {
            "abiertos_you": abiertos_you,
            "abiertos_groups": abiertos_groups,
            "bien": bien,
            "mal": mal,
            "solventado": solventado,
        }
    }
    return render(request, "tickets/home.html", context)

@login_required
def tickets_list(request):
    if not _is_agent(request.user):
        tickets = Ticket.objects.filter(
            Q(requester=request.user) | Q(ccs=request.user)
        ).distinct()
        return render(request, "tickets/tickets_list.html", {"tickets": tickets, "filtros": {}})

    tickets = Ticket.objects.all()

    filtros = {
        "telefonica_mes": tickets.filter(service="Telefonica", created_at__gte=timezone.now()-timedelta(days=30)).count(),
        "unsolved_no_tareas": tickets.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea")).count(),
        "unassigned": tickets.filter(assignee__isnull=True).count(),
        "all_unsolved_no_tareas": tickets.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea")).count(),
        "recently_updated": tickets.order_by("-updated_at")[:100].count(),
        "recently_solved": tickets.filter(status="resolved").order_by("-updated_at")[:100].count(),
        "pendientes": tickets.filter(status="pending").count(),
        "tareas": tickets.filter(type="tarea").count(),
        "unsolved_groups": tickets.filter(~Q(status__in=["closed", "resolved"]),assignee__group=request.user.group).count(),
        "rated_last7": 0,
        "internos_comuny": tickets.filter(service="Comunycarse", status="open").count(),
        "abiertos_ecomfax": tickets.filter(service="ecomfax", status="open").count(),
        "recordia_sgsd": tickets.filter(service="Recordia SGSD", status="open").count(),
        "closed": tickets.filter(status="closed").count(),
        "sus_pendientes": tickets.filter(requester=request.user, status="pending").count(),
        "espera": tickets.filter(status="espera").count() if hasattr(Ticket, "espera") else 0,
        "abiertos": tickets.filter(status="open").count(),
        "sus_no_cerrados": tickets.filter(requester=request.user).exclude(status="closed").count(),
        "ultimos_cerrados": tickets.filter(status="closed").order_by("-updated_at")[:100].count(),
        "no_resueltos": tickets.exclude(status="resolved").count(),
        "twitter": tickets.filter(channel="twitter").count(),
        "twitter_dm": tickets.filter(channel="twitter_dm").count(),
        "twitter_like": tickets.filter(channel="twitter_like").count(),
        "sus_tareas": tickets.filter(requester=request.user, type="tarea").count(),
        "resueltos": tickets.filter(status="resolved").count(),
        "new_in_groups": tickets.filter(assignee__group=request.user.group,created_at__gte=timezone.now()-timedelta(days=7)).count(),
        "open": tickets.filter(status="open").count(),
        "no_update_48h": tickets.filter(updated_at__lte=timezone.now()-timedelta(hours=48)).count(),
    }

    return render(request, "tickets/tickets_list.html", {"tickets": tickets, "filtros": filtros})

@login_required
def filter_tickets(request):
    """
    Devuelve los tickets filtrados en formato JSON según el 'view' seleccionado en la barra lateral.
    """
    view = request.GET.get("view")
    if _is_agent(request.user):
        tickets = Ticket.objects.all()
    else:
        tickets = Ticket.objects.filter(
            Q(requester=request.user) | Q(ccs=request.user)
        ).distinct()

    filtros = {
        "telefonica_mes": tickets.filter(service="Telefonica", created_at__gte=timezone.now()-timedelta(days=30)).count(),
        "unsolved_no_tareas": tickets.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea")).count(),
        "unassigned": tickets.filter(assignee__isnull=True).count(),
        "all_unsolved_no_tareas": tickets.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea")).count(),
        "recently_updated": tickets.order_by("-updated_at")[:100].count(),
        "recently_solved": tickets.filter(status="resolved").order_by("-updated_at")[:100].count(),
        "pendientes": tickets.filter(status="pending").count(),
        "tareas": tickets.filter(type="tarea").count(),
        "unsolved_groups": tickets.filter(~Q(status__in=["closed", "resolved"]),assignee__group=request.user.group).count(),
        "rated_last7": 0,
        "internos_comuny": tickets.filter(service="Comunycarse", status="open").count(),
        "abiertos_ecomfax": tickets.filter(service="ecomfax", status="open").count(),
        "recordia_sgsd": tickets.filter(service="Recordia SGSD", status="open").count(),
        "closed": tickets.filter(status="closed").count(),
        "sus_pendientes": tickets.filter(requester=request.user, status="pending").count(),
        "espera": tickets.filter(status="espera").count() if hasattr(Ticket, "espera") else 0,
        "abiertos": tickets.filter(status="open").count(),
        "sus_no_cerrados": tickets.filter(requester=request.user).exclude(status="closed").count(),
        "ultimos_cerrados": tickets.filter(status="closed").order_by("-updated_at")[:100].count(),
        "no_resueltos": tickets.exclude(status="resolved").count(),
        "twitter": tickets.filter(channel="twitter").count(),
        "twitter_dm": tickets.filter(channel="twitter_dm").count(),
        "twitter_like": tickets.filter(channel="twitter_like").count(),
        "sus_tareas": tickets.filter(requester=request.user, type="tarea").count(),
        "resueltos": tickets.filter(status="resolved").count(),
        "new_in_groups": tickets.filter(assignee__group=request.user.group,created_at__gte=timezone.now()-timedelta(days=7)).count(),
        "open": tickets.filter(status="open").count(),
        "no_update_48h": tickets.filter(updated_at__lte=timezone.now()-timedelta(hours=48)).count(),
    }

    if view == "telefonica_mes":
        tickets = tickets.filter(service="Telefonica", created_at__gte=timezone.now()-timedelta(days=30))
    elif view == "unsolved_no_tareas":
        tickets = tickets.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea"))
    elif view == "unassigned":
        tickets = tickets.filter(assignee__isnull=True)
    elif view == "all_unsolved_no_tareas":
        tickets = tickets.filter(~Q(status__in=["closed", "resolved"]), ~Q(type="tarea"))
    elif view == "recently_updated":
        tickets = tickets.order_by("-updated_at")[:100]
    elif view == "recently_solved":
        tickets = tickets.filter(status="resolved").order_by("-updated_at")[:100]
    elif view == "pendientes":
        tickets = tickets.filter(status="pending")
    elif view == "tareas":
        tickets = tickets.filter(type="tarea")
    elif view == "unsolved_groups":
        tickets = tickets.filter(~Q(status__in=["closed", "resolved"]), assignee__group=request.user.group)
    elif view == "rated_last7":
        tickets = tickets.none()  # placeholder si aún no tienes ratings
    elif view == "internos_comuny":
        tickets = tickets.filter(service="Comunycarse", status="open")
    elif view == "abiertos_ecomfax":
        tickets = tickets.filter(service="ecomfax", status="open")
    elif view == "recordia_sgsd":
        tickets = tickets.filter(service="Recordia SGSD", status="open")
    elif view == "closed":
        tickets = tickets.filter(status="closed")
    elif view == "sus_pendientes":
        tickets = tickets.filter(requester=request.user, status="pending")
    elif view == "espera":
        tickets = tickets.filter(status="espera") if hasattr(Ticket, "espera") else tickets.none()
    elif view == "abiertos":
        tickets = tickets.filter(status="open")
    elif view == "sus_no_cerrados":
        tickets = tickets.filter(requester=request.user).exclude(status="closed")
    elif view == "ultimos_cerrados":
        tickets = tickets.filter(status="closed").order_by("-updated_at")[:100]
    elif view == "no_resueltos":
        tickets = tickets.exclude(status="resolved")
    elif view == "twitter":
        tickets = tickets.filter(channel="twitter")
    elif view == "twitter_dm":
        tickets = tickets.filter(channel="twitter_dm")
    elif view == "twitter_like":
        tickets = tickets.filter(channel="twitter_like")
    elif view == "sus_tareas":
        tickets = tickets.filter(requester=request.user, type="tarea")
    elif view == "resueltos":
        tickets = tickets.filter(status="resolved")
    elif view == "new_in_groups":
        tickets = tickets.filter(assignee__group=request.user.group, created_at__gte=timezone.now()-timedelta(days=7))
    elif view == "open":
        tickets = tickets.filter(status="open")
    elif view == "no_update_48h":
        tickets = tickets.filter(updated_at__lte=timezone.now()-timedelta(hours=48))
    else:
        tickets = Ticket.objects.all()  # fallback

    # serialización para la tabla
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
        for t in tickets
    ]
    return JsonResponse({"tickets": data, "filtros": filtros})

@login_required
def filter_customers(request):
    if not _is_agent(request.user) and not _is_admin(request.user):
         return JsonResponse({"error": "No permission"}, status=403)

    view = request.GET.get("view")

    # Base para la consulta
    base_queryset = User.objects.all()

    # Si es solo agente y no admin, podríamos hacer que solo vea "End user" 
    # pero según requerimientos los agentes deberían ver tanto "end user" como otros operadores.
    # Por ahora dejamos que vean todos, y el controlador visual filtrará (u otro mecanismo).
    if not _is_admin(request.user):
         base_queryset = base_queryset.exclude(role__role_name__in=['admin', 'administrator'])

    if view == "suspended":
        users = base_queryset.filter(group__group_name="Suspended")
    else:  # "all"
        users = base_queryset.exclude(group__group_name="Suspended")

    # contadores
    filtros = {
        "all": base_queryset.exclude(group__group_name="Suspended").count(),
        "suspended": base_queryset.filter(group__group_name="Suspended").count(),
    }

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
        for u in users
    ]

    return JsonResponse({"customers": data, "filtros": filtros})

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
        'empresa': ticket.brand,
        'solicitante': ticket.requester_id,
        'asignado': ticket.assignee_id,
        'ccs': [user.id for user in ticket.ccs.all()],
        'tags': [tag.id for tag in ticket.tags.all()],
        'tipo': ticket.type,
        'prioridad': ticket.priority,
        'servicio': ticket.service,
        'canal': ticket.channel,
        'idioma': ticket.language,
        'categoria': ticket.category,
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
        priority = (request.POST.get('prioridad') or 'normal').strip().lower()

        # Mapear placeholders o valores no válidos a defaults
        if status not in ('open', 'pending', 'closed', 'resolved'):
            status = 'open'
        if priority not in ('low', 'normal', 'high', 'urgent'):
            priority = 'normal'
        if not subject:
            subject = '(sin asunto)'          # nunca NULL en DB
        # description en modelo es not null: mínimo cadena vacía
        description = content or ''

        empresa   = request.POST.get('empresa') or None
        language  = request.POST.get('idioma') or None
        category  = request.POST.get('categoria') or None
        channel   = request.POST.get('canal') or None
        service   = request.POST.get('servicio') or None
        tipo      = request.POST.get('tipo') or None

        solicitante_id = request.POST.get('solicitante') or request.user.id
        asignado_id    = request.POST.get('asignado')
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
            ticket.subject     = subject
            ticket.description = description
            ticket.assignee_id = asignado_id
            ticket.brand       = empresa
            ticket.type        = tipo
            ticket.channel     = channel
            ticket.service     = service
            ticket.language    = language
            ticket.category    = category
            ticket.priority    = priority
            ticket.status      = status
            ticket.save()
            _set_m2m(ticket)
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
            created_by_id=request.user.id,
            brand=empresa,
            type=tipo,
            channel=channel,
            service=service,
            language=language,
            created_at=timezone.now(),
            category=category,
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
    usuarios = User.objects.filter(group=1)
    agentes = User.objects.filter(group=2)
    tags = TicketTag.objects.all()
    todos = User.objects.all()

    ticket_obj = get_object_or_404(Ticket, id=int(id_param)) if id_param and id_param.isdigit() else None

    # --- Historial (interacciones) del solicitante: sus tickets más recientes ---
    user_timeline = []
    if ticket_obj and ticket_obj.requester_id:
        # Trae los 30 más recientes; incluye el actual
        related = (Ticket.objects
                   .filter(requester_id=ticket_obj.requester_id)
                   .select_related('requester', 'assignee')
                   .order_by('-updated_at')[:30])

        for t in related:
            # Último comentario público (snippet)
            last_pub = (Comment.objects
                        .filter(ticket_id=t.id, is_public=True)
                        .order_by('-created_at')
                        .values('content', 'created_at')
                        .first())
            snippet = ''
            if last_pub:
                raw = last_pub['content'] or ''
                snippet = (raw[:140] + '…') if len(raw) > 140 else raw

            user_timeline.append({
                'id': t.id,
                'subject': t.subject,
                'status': t.status,
                'updated_at': t.updated_at.strftime('%d/%m/%Y %H:%M'),
                'last_comment': snippet,
            })
    # prepara comentarios según rol
    if ticket_obj:
        comments_qs = ticket_obj.comment_set.order_by("created_at") if _is_agent(request.user) \
                      else ticket_obj.comment_set.filter(is_public=True).order_by("created_at")
    else:
        comments_qs = Comment.objects.none()

    context = {
        'usuarios': usuarios,
        'agentes': agentes,
        'tags': tags,
        'todos': todos,
        'username': request.user.name,
        'email': request.user.email,
        'ticket': ticket_obj,
        'comments': comments_qs,                    
        'can_use_internal': _is_agent(request.user),
        'user_timeline': user_timeline,
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
        # historial
        TicketHistory.objects.create(
            ticket=ticket,
            previous_status=prev,
            new_status=new_status,
            changed_by=request.user
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
            .order_by('-updated_at')
            .distinct()[:10]
        )
    else:
        tickets = (
            Ticket.objects
            .filter(Q(requester=request.user) | Q(ccs=request.user))
            .order_by('-updated_at')
            .distinct()[:10]
        )
    data = [
        {
            "ticket_id": t.id,
            "message": f"#{t.id} {t.subject}",
            "updated_at": t.updated_at.strftime("%d/%m %H:%M") if t.updated_at else "",
            "status": t.status,
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
def global_search(request):
    q = (request.GET.get("q") or "").strip()
    if not q:
        return JsonResponse({"tickets": [], "users": []})

    # --- Tickets ---
    ticket_qs = Ticket.objects.all() if _is_agent(request.user) else \
        Ticket.objects.filter(Q(requester=request.user) | Q(ccs=request.user)).distinct()

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
