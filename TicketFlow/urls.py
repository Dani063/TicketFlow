"""
Definition of urls for TicketFlow.
"""

from datetime import datetime
from django.urls import path, include
from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from app import forms, views

# -*- coding: utf-8 -*-

urlpatterns = [
    path('', views.home, name='home'),  # P�gina principal
    # Funcionalidades de Ticket
    path('tickets/', views.tickets_list, name='tickets_list'),  # Listar tickets
    path('tickets/create/undefined', views.tickets_list, name='tickets_list'),
    path('tickets/create/', views.create_ticket, name='create_ticket'),
    path('tickets/<int:pk>/assign/', views.assign_agent, name='assign_agent'),
    path('tickets/<int:pk>/update/', views.update_ticket, name='update_ticket'),
    path('tickets/<int:pk>/close/', views.close_ticket, name='close_ticket'),
    path('tickets/<int:pk>/reopen/', views.reopen_ticket, name='reopen_ticket'),
    
    # Funcionalidades de Comentarios
    path('tickets/<int:ticket_pk>/comments/add/', views.add_comment, name='add_comment'),
    
    # Funcionalidades de Gesti�n de Usuarios
    path('admin/', admin.site.urls),

    # Customers
    path('customers/', views.customers_list, name='customers_list'),  # Pagina de clientes
    path('customers/undefined', views.tickets_list, name='tickets_list'),
    #Reporting
    path('reporting/', views.reporting, name='reporting'),  # Pagina con datos y reportes
    path('reporting/undefined', views.tickets_list, name='tickets_list'),
    #Admin
    path('settings/', views.settings, name='settings'),  # Pagina de ajustes
    path('settings/undefined', views.tickets_list, name='tickets_list'),
    #Profile
    path('profile/', views.profile, name='profile'),  # Pagina de perfil
    path('profile/undefined', views.tickets_list, name='tickets_list'),
    #Login
    path('login/', views.login, name='login'),  # Pagina de inicio de sesi�n
    path('login/undefined', views.tickets_list, name='tickets_list'),

    path('api/register/', views.register, name='register'),
    path('api/login/', views.user_login, name='login'),

    path('logout/', views.user_logout, name='logout'),

]
