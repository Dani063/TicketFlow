"""
Definition of models.
"""

from unicodedata import category
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin

class Organization(models.Model):
    zendesk_id = models.BigIntegerField(unique=True, null=True, blank=True, db_index=True)
    name = models.CharField(max_length=255)
    domain_names = models.TextField(blank=True)
    created_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.name


class Brand(models.Model):
    zendesk_id = models.BigIntegerField(unique=True, null=True, blank=True, db_index=True)
    name = models.CharField(max_length=255)
    support_email = models.EmailField(blank=True, default='')
    from_name = models.CharField(max_length=100, blank=True, default='')
    language = models.CharField(max_length=10, blank=True, default='es')
    mailbox_type = models.CharField(
        max_length=10, default='m365',
        choices=[('m365', 'Microsoft 365'), ('ses', 'Amazon SES')],
    )

    def __str__(self):
        return self.name


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

        # Asignar rol por defecto (End user) si existe
        # Importamos el modelo internamente para evitar circular dependencies si fuera el caso,
        # aunque aquí ya está en el mismo archivo.
        try:
            default_role, _ = Role.objects.get_or_create(role_name="End user")
            user.role = default_role
            user.save(using=self._db)
        except Exception:
            pass # Si falla al crear el rol (ej. durante la migración inicial), simplemente lo ignoramos

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
    zendesk_id = models.BigIntegerField(null=True, blank=True, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True, blank=True, related_name='user_group')
    role = models.ForeignKey('Role', on_delete=models.SET_NULL, null=True, blank=True, related_name='user_role')
    phone = models.CharField(max_length=64, null=True, blank=True)
    time_zone = models.CharField(max_length=100, null=True, blank=True)
    locale = models.CharField(max_length=20, null=True, blank=True)
    notes = models.TextField(null=True, blank=True)
    photo_url = models.URLField(max_length=500, null=True, blank=True)
    organization = models.ForeignKey('Organization', on_delete=models.SET_NULL, null=True, blank=True, related_name='users')
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
    # Campo real de staff para acceso a admin, sin atarlo a superuser
    is_staff = models.BooleanField(
        default=False,
        help_text='Permite acceder al admin de Django.'
     )
    is_active = models.BooleanField(
        default=True,
        help_text='Indica si la cuenta está activa. Desmárcalo para deshabilitarla.'
    )
class Role(models.Model):
    role_name = models.CharField(max_length=255, unique=True, null=False)
    description = models.TextField(null=True)

    def __str__(self):
        return self.role_name

class Group(models.Model):
    group_name = models.CharField(max_length=255, null=False)
    description = models.TextField(null=True)
    zendesk_id = models.BigIntegerField(unique=True, null=True, blank=True, db_index=True)

    def __str__(self):
        return self.group_name

class Ticket(models.Model):
    zendesk_id = models.BigIntegerField(null=True, blank=True, unique=True, db_index=True)
    subject = models.CharField(max_length=255, null=False)
    description = models.TextField(null=False)
    status = models.CharField(max_length=255, choices=[('open', 'Open'), ('pending', 'Pending'), ('closed', 'Closed'), ('resolved', 'Resolved')])
    priority = models.CharField(max_length=255, choices=[('low', 'Low'), ('normal', 'Normal'), ('high', 'High'), ('urgent', 'Urgent')], null=True, blank=True)
    requester = models.ForeignKey(User, on_delete=models.CASCADE, related_name='requested_tickets')
    assignee = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='assigned_tickets')
    created_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_tickets')
    brand = models.ForeignKey('Brand', on_delete=models.SET_NULL, null=True, blank=True, related_name='tickets')
    type = models.CharField(max_length=255, null=True)
    ccs = models.ManyToManyField('User', related_name='tickets_ccd', blank=True)
    tags = models.ManyToManyField('TicketTag', related_name='tickets', blank=True)
    channel = models.CharField(max_length=255, null=True)
    service = models.CharField(max_length=255, null=True)
    language = models.CharField(max_length=255, null=True)
    assigned_group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True, blank=True, related_name='tickets')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    category = models.CharField(max_length=255, null=True)
    security_related = models.BooleanField(null=True, blank=True)
    monitoring = models.BooleanField(null=True, blank=True)
    approval_status = models.CharField(max_length=255, null=True, blank=True)
    resolution_type = models.CharField(max_length=255, null=True, blank=True)
    required_tasks = models.CharField(max_length=255, null=True, blank=True)
    merged_into = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='merged_tickets'
    )
    email_message_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)


class ZendeskFieldMap(models.Model):
    """Mapea custom fields de Zendesk con atributos del modelo Ticket.

    Permite tanto importar (zendesk_id -> ticketflow_attr) como exportar
    (ticketflow_attr -> zendesk_id en el payload PUT /tickets/{id}).
    Se popula con el management command `sync_zendesk_fields`.
    """
    zendesk_field_id = models.BigIntegerField(unique=True, db_index=True)
    zendesk_title = models.CharField(max_length=255)
    zendesk_type = models.CharField(max_length=50)  # tagger, checkbox, integer, date, ...
    ticketflow_attr = models.CharField(max_length=100, null=True, blank=True)
    active = models.BooleanField(default=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.zendesk_title} (#{self.zendesk_field_id}) -> {self.ticketflow_attr or '(unmapped)'}"


class Comment(models.Model):
    zendesk_id = models.BigIntegerField(null=True, blank=True, unique=True, db_index=True)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE)
    user = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField(null=False)
    html_body = models.TextField(null=True, blank=True)
    via_channel = models.CharField(max_length=100, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_public = models.BooleanField(default=True)
    email_message_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)

class TicketTag(models.Model):
    name = models.CharField(max_length=255, unique=True, null=False, help_text='Label for categorizing tickets')
    def __str__(self):
        return self.name


class TicketEvent(models.Model):
    """Registro de cambios en un ticket (Zendesk audits o cambios locales)."""
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='events')
    actor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='ticket_events')
    field_name = models.CharField(max_length=100)   # status, assignee_id, priority, group_id, tags…
    old_value = models.TextField(null=True, blank=True)
    new_value = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField()
    zendesk_event_id = models.BigIntegerField(null=True, blank=True, unique=True, db_index=True)

    class Meta:
        ordering = ['created_at']

class Attachment(models.Model):
    file_url = models.URLField(null=False, help_text='URL of the attached file')
    file_type = models.CharField(max_length=255, null=True)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, null=True, blank=True)
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, null=True, blank=True)
    uploaded_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    # models.py
class Notification(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="notifications")
    message = models.CharField(max_length=255)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    read = models.BooleanField(default=False)

    def __str__(self):
        return f"{self.user.name} → {self.message}"


class Macro(models.Model):
    name = models.CharField(max_length=255)
    description = models.CharField(max_length=500, blank=True, null=True)
    actions = models.JSONField(default=dict, help_text='JSON: {status, comment, priority, assignee_id}')
    active = models.BooleanField(default=True)
    zendesk_id = models.BigIntegerField(null=True, blank=True, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class SatisfactionRating(models.Model):
    SCORE_CHOICES = [
        ('offered',    'Offered'),
        ('unoffered',  'Unoffered'),
        ('good',       'Good'),
        ('bad',        'Bad'),
    ]
    zendesk_id  = models.BigIntegerField(unique=True, db_index=True)
    ticket      = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='satisfaction_ratings')
    score       = models.CharField(max_length=50, choices=SCORE_CHOICES)
    comment     = models.TextField(null=True, blank=True)
    reason      = models.CharField(max_length=255, null=True, blank=True)
    requester   = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='satisfaction_ratings_requester')
    assignee    = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                    related_name='satisfaction_ratings_assignee')
    created_at  = models.DateTimeField()
    updated_at  = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Rating #{self.zendesk_id}: {self.score}"
