"""
Definition of models.
"""

from unicodedata import category
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin

from app.constants import CHANNEL_CHOICES, TICKET_TYPE_CHOICES

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
    type = models.CharField(max_length=255, null=True, blank=True, choices=TICKET_TYPE_CHOICES)
    # Un problema (type='problem') agrupa los incidentes que provoca (compatible con problem_id de Zendesk).
    problem = models.ForeignKey(
        'self', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='incidents',
        limit_choices_to={'type': 'problem'},
    )
    ccs = models.ManyToManyField('User', related_name='tickets_ccd', blank=True)
    tags = models.ManyToManyField('TicketTag', related_name='tickets', blank=True)
    channel = models.CharField(max_length=255, null=True, blank=True, choices=CHANNEL_CHOICES)
    service = models.CharField(max_length=255, null=True)
    language = models.CharField(max_length=255, null=True)
    assigned_group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True, blank=True, related_name='tickets')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    due_at = models.DateTimeField(null=True, blank=True)
    first_response_due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    first_responded_at = models.DateTimeField(null=True, blank=True)
    resolution_due_at = models.DateTimeField(null=True, blank=True, db_index=True)
    sla_breached_at = models.DateTimeField(null=True, blank=True, db_index=True)
    # Cumplimiento de primera respuesta, persistido al responder (record_first_response
    # limpia first_response_due_at, así que no es computable a posteriori). NULL = no medido.
    first_response_met = models.BooleanField(null=True, blank=True)
    # Marcadores de idempotencia para aviso de riesgo y escalado de SLA.
    sla_risk_notified_at = models.DateTimeField(null=True, blank=True)
    sla_escalated_at = models.DateTimeField(null=True, blank=True)
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
    email_conversation_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    is_deleted = models.BooleanField(default=False, db_index=True)

    class Meta:
        indexes = [
            models.Index(fields=['is_deleted', 'updated_at'], name='app_ticket_deleted_updated_idx'),
            models.Index(fields=['is_deleted', 'status'], name='app_ticket_deleted_status_idx'),
            models.Index(fields=['is_deleted', 'channel'], name='app_ticket_deleted_channel_idx'),
            models.Index(fields=['is_deleted', 'service'], name='app_ticket_deleted_service_idx'),
            models.Index(fields=['is_deleted', 'merged_into', 'updated_at'], name='app_ticket_live_updated_idx'),
            models.Index(fields=['is_deleted', 'merged_into', 'status', 'updated_at'], name='app_ticket_live_status_upd'),
            models.Index(fields=['is_deleted', 'merged_into', 'assignee', 'updated_at'], name='app_ticket_live_assignee_idx'),
            models.Index(fields=['is_deleted', 'merged_into', 'requester', 'updated_at'], name='app_ticket_live_requester_idx'),
            models.Index(fields=['is_deleted', 'merged_into', 'type', 'updated_at'], name='app_ticket_live_type_upd'),
            models.Index(fields=['is_deleted', 'merged_into', 'channel', 'updated_at'], name='app_ticket_live_channel'),
            models.Index(fields=['is_deleted', 'merged_into', 'service', 'status', 'updated_at'], name='app_ticket_live_service'),
            models.Index(fields=['is_deleted', 'merged_into', 'created_at', 'updated_at'], name='app_ticket_live_created'),
            models.Index(fields=['is_deleted', 'merged_into', 'brand', 'status'], name='app_ticket_live_brand_idx'),
        ]


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

    class Meta:
        indexes = [
            models.Index(fields=['ticket', 'created_at'], name='app_comment_ticket_created_idx'),
            models.Index(fields=['ticket', 'id'], name='app_comment_ticket_id_idx'),
        ]

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


class AssignmentRule(models.Model):
    name = models.CharField(max_length=255)
    active = models.BooleanField(default=True, db_index=True)
    group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True, blank=True, related_name='assignment_rules')
    service = models.CharField(max_length=255, null=True, blank=True)
    channel = models.CharField(max_length=255, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class AssignmentRuleMember(models.Model):
    rule = models.ForeignKey(AssignmentRule, on_delete=models.CASCADE, related_name='members')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assignment_rule_memberships')
    weight = models.PositiveIntegerField(default=1)
    capacity = models.PositiveIntegerField(null=True, blank=True)
    active = models.BooleanField(default=True, db_index=True)

    class Meta:
        unique_together = [('rule', 'user')]

    def __str__(self):
        return f"{self.rule} -> {self.user}"


class SLAPolicy(models.Model):
    name = models.CharField(max_length=255)
    active = models.BooleanField(default=True, db_index=True)
    priority = models.CharField(max_length=255, null=True, blank=True)
    service = models.CharField(max_length=255, null=True, blank=True)
    brand = models.ForeignKey('Brand', on_delete=models.SET_NULL, null=True, blank=True, related_name='sla_policies')
    # Organización del SOLICITANTE (cliente). Permite SLA por cliente: p.ej.
    # Telefónica 2h de primera respuesta, resto 24h.
    organization = models.ForeignKey('Organization', on_delete=models.SET_NULL, null=True, blank=True, related_name='sla_policies')
    assigned_group = models.ForeignKey('Group', on_delete=models.SET_NULL, null=True, blank=True, related_name='sla_policies')
    first_response_minutes = models.PositiveIntegerField(default=0)
    resolution_minutes = models.PositiveIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class AutomationRule(models.Model):
    name = models.CharField(max_length=255)
    active = models.BooleanField(default=True, db_index=True)
    priority = models.PositiveIntegerField(default=100)
    conditions = models.JSONField(default=dict, blank=True)
    actions = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['priority', 'name']

    def __str__(self):
        return self.name


class AutomationExecution(models.Model):
    rule = models.ForeignKey(AutomationRule, on_delete=models.CASCADE, related_name='executions')
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='automation_executions')
    event_key = models.CharField(max_length=255)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = [('rule', 'ticket', 'event_key')]


class InboundEmailLog(models.Model):
    STATUS_RECEIVED = 'received'
    STATUS_PROCESSED = 'processed'
    STATUS_DUPLICATE = 'duplicate'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_RECEIVED, 'Received'),
        (STATUS_PROCESSED, 'Processed'),
        (STATUS_DUPLICATE, 'Duplicate'),
        (STATUS_FAILED, 'Failed'),
    ]

    brand = models.ForeignKey('Brand', on_delete=models.SET_NULL, null=True, blank=True, related_name='inbound_email_logs')
    ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True, blank=True, related_name='inbound_email_logs')
    message_id = models.CharField(max_length=255, db_index=True)
    conversation_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    status = models.CharField(max_length=32, choices=STATUS_CHOICES, default=STATUS_RECEIVED, db_index=True)
    result = models.CharField(max_length=64, null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)
    processed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        unique_together = [('brand', 'message_id')]
        ordering = ['-received_at']

    def __str__(self):
        return f"{self.message_id} ({self.status})"


class ResponseTemplate(models.Model):
    """Plantilla de respuesta al cliente, por clave + marca + idioma.

    brand=NULL es la plantilla global por defecto; una fila con brand concreta
    la sobreescribe para esa marca. Variables tipo {{ticket_id}} se renderizan
    con el motor de plantillas de Django sobre un contexto de escalares.
    """
    key = models.SlugField(max_length=64, db_index=True)  # p.ej. 'ticket_created'
    brand = models.ForeignKey('Brand', on_delete=models.CASCADE, null=True, blank=True,
                              related_name='response_templates')
    language = models.CharField(max_length=5, default='es',
                                choices=[('es', 'Español'), ('en', 'English')])
    subject = models.CharField(max_length=255)
    body_html = models.TextField()
    body_text = models.TextField(blank=True, default='')  # vacío => derivado de body_html
    active = models.BooleanField(default=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = [('key', 'brand', 'language')]
        ordering = ['key', 'language']

    def __str__(self):
        scope = self.brand.name if self.brand_id and self.brand else 'global'
        return f"{self.key} [{scope}/{self.language}]"


class OutboundEmailLog(models.Model):
    STATUS_QUEUED = 'queued'
    STATUS_SENT = 'sent'
    STATUS_FAILED = 'failed'
    STATUS_SUPPRESSED = 'suppressed'
    STATUS_CHOICES = [
        (STATUS_QUEUED, 'Queued'),
        (STATUS_SENT, 'Sent'),
        (STATUS_FAILED, 'Failed'),
        (STATUS_SUPPRESSED, 'Suppressed'),
    ]

    brand = models.ForeignKey('Brand', on_delete=models.SET_NULL, null=True, blank=True,
                              related_name='outbound_email_logs')
    ticket = models.ForeignKey(Ticket, on_delete=models.SET_NULL, null=True, blank=True,
                               related_name='outbound_email_logs')
    template_key = models.CharField(max_length=64, blank=True, default='')
    provider = models.CharField(max_length=10, blank=True, default='',
                                choices=[('m365', 'Microsoft 365'), ('ses', 'Amazon SES')])
    to_email = models.EmailField()
    subject = models.CharField(max_length=255, blank=True, default='')
    payload = models.JSONField(default=dict, blank=True)  # snapshot de cuerpo/headers renderizados
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_QUEUED, db_index=True)
    result = models.CharField(max_length=64, null=True, blank=True)  # 'sent' o motivo de supresión
    error = models.TextField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    provider_message_id = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    # 190 chars: límite de índice unique utf8mb4 en MySQL
    dedup_key = models.CharField(max_length=190, unique=True, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.template_key or 'email'} -> {self.to_email} ({self.status})"


class TicketAIAnalysis(models.Model):
    """Resultado de un análisis de IA sobre un ticket (clasificación ITIL).

    Modelo separado de Ticket a propósito: conserva el histórico entre
    re-clasificaciones y congela la sugerencia de la IA en el tiempo, que es
    exactamente lo que necesita el feedback loop del Q3 (comparar sugerencia
    vs corrección humana en TicketEvent).
    """
    KIND_CLASSIFICATION = 'classification'
    STATUS_PENDING = 'pending'
    STATUS_SUCCESS = 'success'
    STATUS_FAILED = 'failed'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_SUCCESS, 'Success'),
        (STATUS_FAILED, 'Failed'),
    ]

    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='ai_analyses')
    kind = models.CharField(max_length=32, default=KIND_CLASSIFICATION, db_index=True)
    status = models.CharField(max_length=16, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)

    suggested_type = models.CharField(max_length=32, null=True, blank=True)      # question|incident|problem|task
    suggested_category = models.CharField(max_length=255, null=True, blank=True)
    suggested_priority = models.CharField(max_length=32, null=True, blank=True)  # low|normal|high|urgent
    suggested_language = models.CharField(max_length=10, null=True, blank=True)  # es|en
    confidence = models.FloatField(null=True, blank=True)            # mínimo de las confianzas por campo
    field_confidences = models.JSONField(default=dict, blank=True)   # {"type": 0.93, ...}
    reasoning = models.TextField(null=True, blank=True)              # justificación breve, visible para agentes
    applied_fields = models.JSONField(default=list, blank=True)      # ["type", "category", ...]

    input_fingerprint = models.CharField(max_length=64, db_index=True, blank=True, default='')
    input_excerpt = models.TextField(blank=True, default='')         # lo que se envió (truncado)
    raw_response = models.JSONField(default=dict, blank=True)
    model_name = models.CharField(max_length=100, blank=True, default='')
    prompt_version = models.CharField(max_length=20, blank=True, default='v1')
    input_tokens = models.IntegerField(null=True, blank=True)
    output_tokens = models.IntegerField(null=True, blank=True)
    latency_ms = models.IntegerField(null=True, blank=True)
    error = models.TextField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['ticket', 'kind', 'status'], name='app_aianalysis_tks_idx')]

    def __str__(self):
        return f"AI {self.kind} ticket #{self.ticket_id} ({self.status})"


class TicketEmbedding(models.Model):
    """Vector de embedding de un ticket para la búsqueda de casos similares.

    Coseno en memoria al volumen actual; la interfaz SimilarTicketIndex permite
    cambiar a OpenSearch k-NN si crece. content_hash evita recomputar si el texto
    no cambió. Base del módulo Q3 de casos similares + feedback de la IA.
    """
    ticket = models.OneToOneField(Ticket, on_delete=models.CASCADE, related_name='embedding')
    vector = models.JSONField(default=list)
    content_hash = models.CharField(max_length=64, db_index=True, blank=True, default='')
    model_name = models.CharField(max_length=100, blank=True, default='')
    dim = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Embedding ticket #{self.ticket_id} (dim={self.dim})"


class OperationalMetric(models.Model):
    name = models.CharField(max_length=100, db_index=True)
    value = models.IntegerField(default=1)
    labels = models.JSONField(default=dict, blank=True)
    recorded_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ['-recorded_at']

    def __str__(self):
        return f"{self.name}={self.value}"
