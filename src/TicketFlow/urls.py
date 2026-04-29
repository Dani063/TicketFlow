"""
Definition of urls for TicketFlow.
"""

from datetime import datetime
from django.urls import path, include
from django.contrib import admin
from django.contrib.auth.views import LoginView, LogoutView
from app import forms, views
from django.conf import settings
from django.conf.urls.static import static

# -*- coding: utf-8 -*-

urlpatterns = [
    path('', views.home, name='home'),
    path('tickets/', views.tickets_list, name='tickets_list'),
    path('tickets/create/', views.create_ticket, name='create_ticket'),
    path('tickets/<int:ticket_id>/add_comment/', views.add_comment, name='add_comment'),
    path('api/tickets/<int:ticket_id>/', views.ticket_detail_api, name='ticket_detail_api'),
    path("tickets/filter/", views.filter_tickets, name="filter_tickets"),
    path('api/tags/', views.tags_api, name='tags_api'),
    path('tickets/<int:ticket_id>/attachments/upload/', views.upload_attachment, name='upload_attachment'),

    path("customers/filter/", views.filter_customers, name="filter_customers"),
    path('customers/', views.customers_list, name='customers_list'),
    path("customers/profile/", views.customer_profile, name="customer_profile"),
    path('reporting/', views.reporting, name='reporting'),
    path('settings/', views.settings, name='settings'),
    path('profile/', views.profile, name='profile'),
    path("api/notifications/", views.notifications_api, name="notifications_api"),
    path("api/notifications/<int:notif_id>/read/", views.mark_notification_read, name="mark_notification_read"),

    path('login/', views.login_redirect, name='login'),
    path('sso/callback/', views.sso_callback, name='sso_callback'),
    path('api/sso/complete/', views.sso_complete, name='sso_complete'),
    path('api/register/', views.register, name='register'),
    path('api/login/', views.user_login, name='api_login'),

    path('logout/', views.user_logout, name='logout'),

    path("search/", views.global_search, name="global_search"),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
