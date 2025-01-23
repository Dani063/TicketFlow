"""
Definition of urls for TicketFlow.
"""

from datetime import datetime
from django.urls import path
from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from app import forms, views

urlpatterns = [
    path('', views.home, name='home'),  # Página principal
    # Funcionalidades de Ticket
    path('tickets/', views.tickets_list, name='tickets_list'),  # Listar tickets
    path('tickets/create/', views.create_ticket, name='create_ticket'),
    path('tickets/<int:pk>/assign/', views.assign_agent, name='assign_agent'),
    path('tickets/<int:pk>/update/', views.update_ticket, name='update_ticket'),
    path('tickets/<int:pk>/close/', views.close_ticket, name='close_ticket'),
    path('tickets/<int:pk>/reopen/', views.reopen_ticket, name='reopen_ticket'),
    
    # Funcionalidades de Comentarios
    path('tickets/<int:ticket_pk>/comments/add/', views.add_comment, name='add_comment'),
    
    # Funcionalidades de Gestión de Usuarios
    path('users/create/', views.create_user, name='create_user'),
    path('admin/', admin.site.urls)

    # Puedes añadir más rutas aquí para las funcionalidades restantes como reportes, búsquedas, etc.
]
