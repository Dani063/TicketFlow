from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Ticket, Comment, User, Role, Group  # Role/Group existen en tu modelo

# ====== Ticket ======

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "id", "subject", "status", "priority",
        "requester", "assignee", "created_at", "updated_at"
    )
    list_filter = ("status", "priority", "type", "service", "channel", "language")
    search_fields = ("subject", "description", "requester__email", "assignee__email")
    date_hierarchy = "created_at"
    ordering = ("-updated_at",)
    list_select_related = ("requester", "assignee")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Evita N+1 al listar tickets
        return qs.select_related("requester", "assignee")

# ====== Comment ======

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "user", "is_public", "created_at")
    search_fields = ("content", "ticket__subject", "user__email", "user__name")
    list_filter = ("is_public",)
    date_hierarchy = "created_at"
    ordering = ("-created_at",)

# ====== User ======

@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    # Campos que se muestran en la lista
    list_display = ("email", "name", "role", "group", "is_staff", "is_superuser", "is_active", "created_at")
    list_filter = ("is_staff", "is_superuser", "is_active", "role", "group")
    search_fields = ("email", "name")
    ordering = ("email",)
    readonly_fields = ("created_at", "updated_at", "last_login")

    # Campos al editar un usuario existente
    fieldsets = (
        (None, {"fields": ("email", "name", "password")}),
        ("Metadatos", {"fields": ("role", "group")}),
        ("Permisos", {"fields": ("is_active", "is_staff", "is_superuser", "groups", "user_permissions")}),
        ("Fechas", {"fields": ("last_login", "created_at", "updated_at")}),
    )

    # Campos al crear un usuario desde el admin
    add_fieldsets = (
        (None, {
            "classes": ("wide",),
            "fields": ("email", "name", "password1", "password2", "is_staff", "is_superuser", "is_active", "role", "group"),
        }),
    )

# ====== Catálogos ======
admin.site.register(Role)
admin.site.register(Group)
