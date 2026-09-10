from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import Brand, Comment, Group, HelpArticle, HelpArticleAsset, HelpArticleRevision, HelpCategory, HelpCenter, HelpSection, OutboundEmailLog, ProductLine, ResponseTemplate, Role, SatisfactionRating, SatisfactionReason, SLAPolicy, Ticket, TicketAIAnalysis, User

# ====== Ticket ======

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "id", "subject", "product_line", "status", "priority",
        "requester", "assignee", "created_at", "updated_at"
    )
    list_filter = ("status", "priority", "product_line", "type", "service", "channel", "language")
    search_fields = ("subject", "description", "requester__email", "assignee__email")
    date_hierarchy = "created_at"
    ordering = ("-updated_at",)
    list_select_related = ("requester", "assignee", "product_line")

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        # Evita N+1 al listar tickets
        return qs.select_related("requester", "assignee", "product_line")

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

# ====== Cat�logos ======
admin.site.register(Role)
admin.site.register(Group)


@admin.register(ProductLine)
class ProductLineAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "color", "icon", "sort_order", "active")
    list_editable = ("color", "icon", "sort_order", "active")
    list_filter = ("active",)
    search_fields = ("name", "code")
    ordering = ("sort_order", "name")


@admin.register(Brand)
class BrandAdmin(admin.ModelAdmin):
    list_display = ("name", "support_email", "from_name", "mailbox_type", "language")
    list_filter = ("mailbox_type",)
    search_fields = ("name", "support_email")


@admin.register(ResponseTemplate)
class ResponseTemplateAdmin(admin.ModelAdmin):
    list_display = ("key", "brand", "language", "subject", "active", "updated_at")
    list_filter = ("active", "language", "brand")
    search_fields = ("key", "subject")
    list_select_related = ("brand",)


@admin.register(SatisfactionReason)
class SatisfactionReasonAdmin(admin.ModelAdmin):
    list_display = ("code", "label", "language", "position", "active")
    list_filter = ("active", "language")
    search_fields = ("code", "label")


@admin.register(SatisfactionRating)
class SatisfactionRatingAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "score", "source", "assignee", "reason_choice",
                    "offered_at", "responded_at", "expires_at")
    list_filter = ("score", "source")
    search_fields = ("ticket__subject", "comment", "reason")
    list_select_related = ("ticket", "assignee", "reason_choice")
    # El token es la credencial del enlace público: no debe editarse a mano.
    readonly_fields = ("token", "zendesk_id")


@admin.register(OutboundEmailLog)
class OutboundEmailLogAdmin(admin.ModelAdmin):
    list_display = ("id", "template_key", "to_email", "brand", "provider", "status", "result", "attempts", "created_at", "sent_at")
    list_filter = ("status", "provider", "template_key")
    search_fields = ("to_email", "subject", "provider_message_id")
    readonly_fields = [f.name for f in OutboundEmailLog._meta.fields]
    list_select_related = ("brand",)

    def has_add_permission(self, request):
        return False


@admin.register(TicketAIAnalysis)
class TicketAIAnalysisAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "kind", "status", "suggested_type", "suggested_priority",
                    "confidence", "model_name", "prompt_version", "latency_ms", "created_at")
    list_filter = ("status", "kind", "suggested_type", "prompt_version")
    search_fields = ("ticket__subject", "reasoning")
    readonly_fields = [f.name for f in TicketAIAnalysis._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(SLAPolicy)
class SLAPolicyAdmin(admin.ModelAdmin):
    list_display = (
        "name", "active", "priority", "service", "brand", "assigned_group",
        "first_response_minutes", "resolution_minutes",
    )
    list_filter = ("active", "priority", "brand")
    search_fields = ("name",)
    list_select_related = ("brand", "assigned_group")


@admin.register(HelpCenter)
class HelpCenterAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "service", "default_locale", "active", "updated_at")
    list_filter = ("active", "default_locale")
    search_fields = ("name", "slug", "service")


@admin.register(HelpCategory)
class HelpCategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "center", "locale", "position", "published")
    list_filter = ("center", "locale", "published")
    search_fields = ("name", "source_id")


@admin.register(HelpSection)
class HelpSectionAdmin(admin.ModelAdmin):
    list_display = ("name", "category", "position", "published")
    list_filter = ("published", "category__center", "category__locale")
    search_fields = ("name", "source_id")
    list_select_related = ("category", "category__center")


@admin.register(HelpArticle)
class HelpArticleAdmin(admin.ModelAdmin):
    list_display = ("title", "section", "origin", "editorial_override", "version", "promoted", "published", "source_updated_at")
    list_filter = ("published", "promoted", "origin", "editorial_override", "section__category__center", "section__category__locale")
    search_fields = ("title", "body_text", "source_id")
    list_select_related = ("section", "section__category", "section__category__center")


@admin.register(HelpArticleRevision)
class HelpArticleRevisionAdmin(admin.ModelAdmin):
    list_display = ("article", "number", "status", "created_by", "created_at", "published_at")
    list_filter = ("status", "section__category__center", "section__category__locale")
    search_fields = ("article__title", "title", "change_note")
    readonly_fields = (
        "article", "number", "section", "slug", "title", "body_html", "body_text",
        "promoted", "position", "status", "base_version", "change_note", "created_by",
        "created_at", "published_at",
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(HelpArticleAsset)
class HelpArticleAssetAdmin(admin.ModelAdmin):
    list_display = ("original_name", "article", "storage_backend", "size", "uploaded_by", "created_at")
    list_filter = ("storage_backend", "article__section__category__center")
    search_fields = ("original_name", "article__title", "checksum")
    readonly_fields = ("id", "article", "original_name", "storage_key", "storage_backend", "content_type", "size", "checksum", "uploaded_by", "created_at")

    def has_add_permission(self, request):
        return False
