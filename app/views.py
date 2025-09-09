"""
Definition of views.
"""
# -*- coding: utf-8 -*-
from django.db.models import Q
from datetime import datetime
from types import new_class
from unicodedata import category
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from .models import Ticket, Comment, User, TicketTag
from .forms import TicketForm, CommentForm, UserForm
from django.contrib.auth.hashers import check_password
from django.contrib.auth.decorators import login_required
from django.contrib.auth import authenticate, login as auth_login
from django.contrib.auth import logout
from django.views.decorators.csrf import csrf_exempt
import json
import logging
from django.views.decorators.http import require_POST
from django.utils import timezone
from django.views import View

# Funcionalidades de Ticket

@login_required
def home(request):
    tickets = Ticket.objects.filter(Q(assignee=request.user) | Q(ccs=request.user)).distinct()
    context = {
        'username': request.user.name,
        'email': request.user.email,
        'tickets': tickets,         
        'my_tickets': tickets,      
    }
    return render(request, 'tickets/home.html', context)

@login_required
def tickets_list(request):
    tickets = Ticket.objects.all()
    context = {
        'username': request.user.name,
        'email': request.user.email,
        'tickets': tickets,
    }
    return render(request, 'tickets/tickets_list.html', context)

@login_required
def customers_list(request):
    context = {
        'username': request.user.name,
        'email': request.user.email,
    }
    customers = User.objects.all()
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/customers_list.html', context)

@login_required
def reporting(request):
    context = {
        'username': request.user.name,
        'email': request.user.email,
    }
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/reporting.html', context)

@login_required
def settings(request):
    context = {
        'username': request.user.name,
        'email': request.user.email,
    }
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/settings.html', context)
   
def profile(request):
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/profile.html')

def login(request):
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/login.html')

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

import random

import random
from django.utils import timezone

import random
from django.utils import timezone

import random
from django.utils import timezone

def ticket_detail_api(request, ticket_id):
    ticket = get_object_or_404(Ticket, id=ticket_id)
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

logger = logging.getLogger('app.views')

@login_required
def create_ticket(request):
    id_param = request.GET.get('id', None)

    if request.method == 'POST':
        # --- Leer y sanear inputs (evita NULL en campos obligatorios) ---
        subject = (request.POST.get('subject') or '').strip()
        content = (request.POST.get('content') or '').strip()
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

            if content:  # si escribiste texto, lo registramos como comentario
                Comment.objects.create(
                    ticket_id=ticket.id,
                    user_id=request.user.id,
                    content=content,
                    created_at=timezone.now()
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

        if content:
            Comment.objects.create(
                ticket_id=ticket.id,
                user_id=request.user.id,
                content=content,
                created_at=timezone.now()
            )

        return redirect(f'{request.path}?id={ticket.id}')

    # GET ...
    usuarios = User.objects.filter(group=1)
    agentes = User.objects.filter(group=2)
    tags = TicketTag.objects.all()
    todos = User.objects.all()

    ticket_obj = get_object_or_404(Ticket, id=int(id_param)) if id_param and id_param.isdigit() else None

    context = {
        'usuarios': usuarios,
        'agentes': agentes,
        'tags': tags,
        'todos': todos,
        'username': request.user.name,
        'email': request.user.email,
        'ticket': ticket_obj,
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
    content = data.get('content', '').strip()
    if not content:
        return JsonResponse({'error': 'El contenido no puede estar vacío.'}, status=400)

    ticket = get_object_or_404(Ticket, id=ticket_id)

    comment = Comment.objects.create(
        ticket=ticket,
        user=request.user,
        content=content,
        created_at=timezone.now()
    )

    return JsonResponse({
        'id': comment.id,
        'content': comment.content,
        'created_at': comment.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'ticket_id': ticket.id,
        'user_id': request.user.id,
        'username': request.user.name,
    })

# Funcionalidades de Gestion de Usuarios

# Funcionalidades de Notificaciones, Reportes, Busquedas y Etiquetas

# Las notificaciones y reportes suelen implementarse como funciones que se ejecutan en segundo plano o como vistas especializadas que generan y muestran los resultados.
# La busqueda y filtrado pueden implementarse como vistas que procesan las consultas del usuario y devuelven los resultados en la misma plantilla.

# Nota: Estas vistas son b�sicas y necesitar�n plantillas HTML para funcionar correctamente.


