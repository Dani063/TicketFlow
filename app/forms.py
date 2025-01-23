# app/forms.py
from django import forms
from .models import Ticket, Comment, User
class TicketForm(forms.ModelForm):
    class Meta:
        model = Ticket
        fields = ['subject', 'description', 'status', 'priority', 'requester', 'assignee', 'group', 'brand', 'type', 'ccs', 'channel'] 

class CommentForm(forms.ModelForm):
    class Meta:
        model = Comment
        fields = ['ticket', 'user', 'content']

class UserForm(forms.ModelForm):
    class Meta:
        Model = User
        fields = ['name', 'email', 'password_hash', 'role', 'group']