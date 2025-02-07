"""
Definition of views.
"""
# -*- coding: utf-8 -*-
from datetime import datetime
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
from django.views.decorators.http import require_POST
from django.utils import timezone

# Funcionalidades de Ticket

@login_required
def home(request):
    tickets = Ticket.objects.all()
    users = User.objects.all()
    context = {
        'username': request.user.name,
        'email': request.user.email,
        'tickets': tickets,
        'users': users,
    }
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/home.html', context)

@login_required
def tickets_list(request):
    context = {
        'username': request.user.name,
        'email': request.user.email,
    }
    tickets = Ticket.objects.all()
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/tickets_list.html', context)

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

def user_logout(request):
    logout(request)  # Cierra la sesión del usuario
    return redirect('/login/')  # Redirige al login
   
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

def create_ticket(request):
    if request.method == 'POST':
        # Obtener los datos del formulario
        empresa = request.POST.get('empresa')
        subject = request.POST.get('subject')
        content = request.POST.get('content')
        language = request.POST.get('idioma')
        category = request.POST.get('categoria')
        channel = request.POST.get('canal')
        service = request.POST.get('servicio')
        type = request.POST.get('tipo')
        priority = request.POST.get('prioridad')
        status = request.POST.get('status')
        solicitante_id = request.POST.get('solicitante')
        asignado_id = request.POST.get('asignado')

        # Obtener el usuario logueado
        requester_id = request.user.id
        
         # Si no se selecciona un solicitante, usar el usuario logueado
        if solicitante_id == '':
            solicitante_id = requester_id
        
        # Si no se selecciona un agente, asignar uno aleatorio
        if asignado_id == '':
            agentes = User.objects.filter(group_id='2')
            asignado_id = random.choice(agentes).id if agentes.exists() else None

        # Crear el ticket
        ticket = Ticket.objects.create(
            subject=subject,
            description=content,
            requester_id=solicitante_id,
            assignee_id=asignado_id,
            created_by_id=requester_id,
            brand=empresa,
            type=type,
            channel=channel,
            service=service,
            language=language,
            created_at=timezone.now(),
            category=category,
            priority=priority,
            status=status
        )

        # Crear el comentario
        Comment.objects.create(
            ticket_id=ticket.id,
            user_id=requester_id,
            content=content,
            created_at=timezone.now()
        )

    usuarios = User.objects.filter(group=1)
    agentes = User.objects.filter(group=2)
    tags = TicketTag.objects.all()
    todos = User.objects.all()
    context = {
        'usuarios': usuarios,
        'agentes': agentes,
        'tags': tags,
        'todos': todos, 
        'username': request.user.name,
        'email': request.user.email,
    }
    return render(request, 'tickets/create_ticket.html', context)

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
def add_comment(request):
    data = json.loads(request.body)
    ticket_id = data.get('ticket_id')
    content = data.get('content')
    user = request.user

    if not content:
        return JsonResponse({'error': 'El contenido no puede estar vacío.'}, status=400)

    ticket = get_object_or_404(Ticket, id=ticket_id)

    comment = Comment.objects.create(
        ticket=ticket,
        user=user,
        content=content,
        created_at=timezone.now()
    )

    return JsonResponse({
        'id': comment.id,
        'content': comment.content,
        'created_at': comment.created_at.strftime('%Y-%m-%d %H:%M:%S'),
        'ticket_id': comment.ticket.id,
        'user_id': comment.user.id,
        'username': comment.user.name
    })
# Funcionalidades de Gestion de Usuarios

# Funcionalidades de Notificaciones, Reportes, Busquedas y Etiquetas

# Las notificaciones y reportes suelen implementarse como funciones que se ejecutan en segundo plano o como vistas especializadas que generan y muestran los resultados.
# La busqueda y filtrado pueden implementarse como vistas que procesan las consultas del usuario y devuelven los resultados en la misma plantilla.

# Nota: Estas vistas son b�sicas y necesitar�n plantillas HTML para funcionar correctamente.


