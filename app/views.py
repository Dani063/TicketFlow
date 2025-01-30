"""
Definition of views.
"""
# -*- coding: utf-8 -*-
from datetime import datetime
from django.shortcuts import render, get_object_or_404, redirect
from django.http import JsonResponse
from .models import Ticket, Comment, User, TicketTag
from .forms import TicketForm, CommentForm, UserForm
from django.contrib.auth.decorators import login_required

# Funcionalidades de Ticket

def home(request):
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/home.html')

def tickets_list(request):
    tickets = Ticket.objects.all()
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/tickets_list.html', {'tickets': tickets})

def customers_list(request):
    customers = User.objects.all()
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/customers_list.html', {'customers': customers})

def reporting(request):
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/reporting.html')

def settings(request):
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/settings.html')

def profile(request):
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/profile.html')

def login(request):
    return render(request, 'C:/Users/molqueda/source/repos/TicketFlow/app/templates/tickets/login.html')

def ticket_detail(request, pk):
    ticket = get_object_or_404(Ticket, pk=pk)
    return render(request, 'tickets/ticket_detail.html', {'ticket': ticket})

def create_ticket(request):
    if request.method == 'POST':
        # Obtener los datos del formulario
        empresa = request.POST.get('empresa')
        solicitante = request.POST.get('solicitante')
        asignado = request.POST.get('asignado')
        ccs = request.POST.getlist('ccs')
        tags = request.POST.getlist('tags')
        servicio = request.POST.get('servicio')
        canal = request.POST.get('canal')
        idioma = request.POST.get('idioma')
        categoria = request.POST.get('categoria')

        # Crear nuevos tags si no existen
        tag_objects = []
        for tag_name in tags:
            tag, created = TicketTag.objects.get_or_create(name=tag_name)
            tag_objects.append(tag)

        # Crear el ticket (asumiendo que tienes un modelo Ticket)
        ticket = Ticket.objects.create(
            empresa=empresa,
            solicitante_id=solicitante,
            asignado_id=asignado,
            servicio=servicio,
            canal=canal,
            idioma=idioma,
            categoria=categoria
        )
        ticket.tags.set(tag_objects)
        ticket.ccs.set(ccs)
        ticket.save()

        return redirect('tickets_list')

    usuarios = User.objects.filter(group=1)
    agentes = User.objects.filter(group=2)
    tags = TicketTag.objects.all()
    todos = User.objects.all()
    context = {
        'usuarios': usuarios,
        'agentes': agentes,
        'tags': tags,
        'todos': todos,
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
    agents = User.objects.filter(role='agent')
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
def add_comment(request, ticket_pk):
    ticket = get_object_or_404(Ticket, pk=ticket_pk)
    if request.method == 'POST':
        form = CommentForm(request.POST, request.FILES)
        if form.is_valid():
            comment = form.save(commit=False)
            comment.ticket = ticket
            comment.user = request.user
            comment.save()
            return redirect('ticket_detail', pk=ticket.pk)
    else:
        form = CommentForm()
    return render(request, 'comments/add_comment.html', {'form': form, 'ticket': ticket})

# Funcionalidades de Gestión de Usuarios

@login_required
def create_user(request):
    if request.method == 'POST':
        form = UserForm(request.POST)
        if form.is_valid():
            form.save()
            return redirect('user_list')
    else:
        form = UserForm()
    return render(request, 'users/create_user.html', {'form': form})

# Funcionalidades de Notificaciones, Reportes, Búsquedas y Etiquetas

# Las notificaciones y reportes suelen implementarse como funciones que se ejecutan en segundo plano o como vistas especializadas que generan y muestran los resultados.
# La búsqueda y filtrado pueden implementarse como vistas que procesan las consultas del usuario y devuelven los resultados en la misma plantilla.

# Nota: Estas vistas son básicas y necesitarán plantillas HTML para funcionar correctamente.
