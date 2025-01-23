from django.contrib import admin
from .models import Ticket, Comment, User

@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('subject', 'status', 'priority', 'created_at', 'updated_at')
    search_fields = ('subject', 'description')
    list_filter = ('status', 'priority', 'type')

@admin.register(Comment)
class CommentAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'user', 'content')
    search_fields = ('content',)

@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ('name', 'email', 'role', 'created_at')
    search_fields = ('name', 'email')
    list_filter = ('role',)
