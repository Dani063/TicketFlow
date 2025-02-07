"""
Definition of models.
"""

from unicodedata import category
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin

class UserManager(BaseUserManager):
    def create_user(self, email, name, password=None):
        if not email:
            raise ValueError('El usuario debe tener un correo electronico')
        if not name:
            raise ValueError('El usuario debe tener un nombre')

        user = self.model(
            email=self.normalize_email(email),
            name=name,
        )

        user.set_password(password)  # Hashear la contraseña
        user.save(using=self._db)
        return user

    def create_superuser(self, email, name, password=None):
        user = self.create_user(email, name, password)
        user.is_staff = True
        user.is_superuser = True
        user.save(using=self._db)
        return user

class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(verbose_name='Correo electronico', max_length=255, unique=True)
    name = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True, blank=True, related_name='user_group')
    role = models.ForeignKey('Role', on_delete=models.SET_NULL, null=True, blank=True, related_name='user_role')
    groups = models.ManyToManyField(
        'auth.Group',
        related_name='custom_user_set',  # Cambia el related_name para evitar conflictos
        blank=True,
        help_text='The groups this user belongs to.',
        verbose_name='groups',
    )
    user_permissions = models.ManyToManyField(
        'auth.Permission',
        related_name='custom_user_permissions_set',  # Cambia el related_name para evitar conflictos
        blank=True,
        help_text='Specific permissions for this user.',
        verbose_name='user permissions',
    )

    objects = UserManager()

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['name']

    def __str__(self):
        return self.email

    def has_perm(self, perm, obj=None):
        return True

    def has_module_perms(self, app_label):
        return True

    @property
    def is_staff(self):
        return self.is_superuser

class Role(models.Model):
    role_name = models.CharField(max_length=255, unique=True, null=False)
    description = models.TextField(null=True)

    def __str__(self):
        return self.role_name

class Group(models.Model):
    group_name = models.CharField(max_length=255, null=False)
    description = models.TextField(null=True)

    def __str__(self):
        return self.group_name

class Ticket(models.Model):
    subject = models.CharField(max_length=255, null=False)
    description = models.TextField(null=False)
    status = models.CharField(max_length=255, choices=[('open', 'Open'), ('pending', 'Pending'), ('closed', 'Closed'), ('resolved', 'Resolved')])
    priority = models.CharField(max_length=255, choices=[('low', 'Low'), ('normal', 'Normal'), ('high', 'High'), ('urgent', 'Urgent')])
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requested_tickets')
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='assigned_tickets')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_tickets')
    brand = models.CharField(max_length=255, null=True)
    type = models.CharField(max_length=255, null=True)
    ccs = models.TextField(null=True, help_text='Comma-separated list of user emails to CC')
    channel = models.CharField(max_length=255, null=True)
    service = models.CharField(max_length=255, null=True)
    language = models.CharField(max_length=255, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    category = models.CharField(max_length=255, null=True)

class Comment(models.Model):
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField(null=False)
    created_at = models.DateTimeField(auto_now_add=True)

class TicketTag(models.Model):
    name = models.CharField(max_length=255, unique=True, null=False, help_text='Label for categorizing tickets')
    def __str__(self):
        return self.name

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
