"""
Definition of models.
"""

from django.db import models

class Role(models.Model):
    role_name = models.CharField(max_length=255, unique=True, null=False)
    description = models.TextField(null=True)

class Group(models.Model):
    group_name = models.CharField(max_length=255, null=False)
    description = models.TextField(null=True)

class User(models.Model):
    name = models.CharField(max_length=255, null=False)
    email = models.EmailField(unique=True, null=False)
    password_hash = models.CharField(max_length=255, null=False, help_text='Hash of the password')
    role = models.ForeignKey(Role, on_delete=models.SET_NULL, null=True)
    group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

class Ticket(models.Model):
    subject = models.CharField(max_length=255, null=False)
    description = models.TextField(null=False)
    status = models.CharField(max_length=255, choices=[('open', 'Open'), ('pending', 'Pending'), ('closed', 'Closed'), ('resolved', 'Resolved')])
    priority = models.CharField(max_length=255, choices=[('low', 'Low'), ('normal', 'Normal'), ('high', 'High'), ('urgent', 'Urgent')])
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requested_tickets')
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='assigned_tickets')
    group = models.ForeignKey(Group, on_delete=models.SET_NULL, null=True)
    brand = models.CharField(max_length=255, null=True)
    type = models.CharField(max_length=255, null=True)
    ccs = models.TextField(null=True, help_text='Comma-separated list of user emails to CC')
    channel = models.CharField(max_length=255, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)

class Comment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField(null=False)
    created_at = models.DateTimeField(auto_now_add=True)

class TicketTag(models.Model):
    name = models.CharField(max_length=255, unique=True, null=False, help_text='Label for categorizing tickets')

class TicketTagAssignment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE)
    tag = models.ForeignKey(TicketTag, on_delete=models.CASCADE)

    class Meta:
        unique_together = ('ticket', 'tag')

class TicketHistory(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE)
    previous_status = models.CharField(max_length=255, null=True)
    new_status = models.CharField(max_length=255, choices=[('open', 'Open'), ('pending', 'Pending'), ('closed', 'Closed'), ('resolved', 'Resolved')])
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    changed_at = models.DateTimeField(auto_now_add=True)

class Attachment(models.Model):
    file_url = models.URLField(null=False, help_text='URL of the attached file')
    file_type = models.CharField(max_length=255, null=True)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, null=True, blank=True)
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, null=True, blank=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)
