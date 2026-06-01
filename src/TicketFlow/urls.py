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
    path("", include("health.urls")),

    path('', views.home, name='home'),
    path('tickets/', views.tickets_list, name='tickets_list'),
    path('tickets/create/', views.create_ticket, name='create_ticket'),
    path('tickets/<int:ticket_id>/add_comment/', views.add_comment, name='add_comment'),
    path('tickets/<int:ticket_id>/take/', views.take_ticket, name='take_ticket'),
    path('tickets/<int:ticket_id>/merge/', views.merge_ticket, name='merge_ticket'),
    path('tickets/bulk_delete/', views.bulk_delete, name='bulk_delete'),
    path('tickets/bulk_merge/', views.bulk_merge, name='bulk_merge'),
    path('api/tickets/<int:ticket_id>/', views.ticket_detail_api, name='ticket_detail_api'),
    path("tickets/filter/", views.filter_tickets, name="filter_tickets"),
    path('api/tags/', views.tags_api, name='tags_api'),
    path('api/users/search/', views.users_search_api, name='users_search_api'),
    path('tickets/<int:ticket_id>/attachments/upload/', views.upload_attachment, name='upload_attachment'),

    path("customers/filter/", views.filter_customers, name="filter_customers"),
    path('customers/', views.customers_list, name='customers_list'),
    path("customers/profile/", views.customer_profile, name="customer_profile"),
    path('reporting/', views.reporting, name='reporting'),
    path('settings/', views.settings, name='settings'),
    # Admin panel (solo accesible para usuarios con rol admin)
    path('admin-panel/', views.admin_panel, name='admin_panel'),
    path('api/admin/users/', views.admin_users_api, name='admin_users_api'),
    path('api/admin/users/<int:user_id>/password/', views.admin_reset_password_api, name='admin_reset_password_api'),
    path('api/admin/roles/', views.admin_roles_api, name='admin_roles_api'),
    path('api/admin/groups/', views.admin_groups_api, name='admin_groups_api'),
    path('profile/', views.profile, name='profile'),
    path('docs/', views.documentation, name='documentation'),
    path("api/users/<int:user_id>/notes/", views.update_user_notes, name="update_user_notes"),
    path("api/notifications/", views.notifications_api, name="notifications_api"),
    path("api/notifications/<int:notif_id>/read/", views.mark_notification_read, name="mark_notification_read"),
    path("api/activity/", views.recent_activity_api, name="recent_activity_api"),

    path('login/', views.login_redirect, name='login'),
    path('dev-login/', views.dev_login, name='dev_login'),
    path('sso/callback/', views.sso_callback, name='sso_callback'),
    path('api/sso/complete/', views.sso_complete, name='sso_complete'),
    path('api/register/', views.register, name='register'),
    path('api/login/', views.user_login, name='api_login'),

    path('logout/', views.user_logout, name='logout'),

    path("search/", views.global_search, name="global_search"),
    path("api/macros/", views.macros_api, name="macros_api"),
]
if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
