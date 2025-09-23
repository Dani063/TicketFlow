# app/forms.py
from django import forms
from .models import Ticket, Comment, User

class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['subject', 'description', 'status', 'priority', 'requester', 'assignee', 'created_by', 'brand', 'type', 'ccs', 'channel', 'service', 'language', 'closed_at']

class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ['ticket', 'user', 'content', 'is_public']

class UserForm(forms.ModelForm):
    class Meta:
        model = User
        fields = ['name', 'email', 'role', 'group']  # Eliminar 'password_hash'
