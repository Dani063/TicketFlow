from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0035_more_live_ticket_filter_indexes'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='first_response_due_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='resolution_due_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='sla_breached_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.CreateModel(
            name='AssignmentRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('service', models.CharField(blank=True, max_length=255, null=True)),
                ('channel', models.CharField(blank=True, max_length=255, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('group', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='assignment_rules', to='app.group')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='AutomationRule',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('priority', models.PositiveIntegerField(default=100)),
                ('conditions', models.JSONField(blank=True, default=dict)),
                ('actions', models.JSONField(blank=True, default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['priority', 'name'],
            },
        ),
        migrations.CreateModel(
            name='OperationalMetric',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(db_index=True, max_length=100)),
                ('value', models.IntegerField(default=1)),
                ('labels', models.JSONField(blank=True, default=dict)),
                ('recorded_at', models.DateTimeField(auto_now_add=True, db_index=True)),
            ],
            options={
                'ordering': ['-recorded_at'],
            },
        ),
        migrations.CreateModel(
            name='SLAPolicy',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=255)),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('priority', models.CharField(blank=True, max_length=255, null=True)),
                ('service', models.CharField(blank=True, max_length=255, null=True)),
                ('first_response_minutes', models.PositiveIntegerField(default=0)),
                ('resolution_minutes', models.PositiveIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('assigned_group', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='sla_policies', to='app.group')),
            ],
            options={
                'ordering': ['name'],
            },
        ),
        migrations.CreateModel(
            name='AssignmentRuleMember',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('weight', models.PositiveIntegerField(default=1)),
                ('capacity', models.PositiveIntegerField(blank=True, null=True)),
                ('active', models.BooleanField(db_index=True, default=True)),
                ('rule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='members', to='app.assignmentrule')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='assignment_rule_memberships', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'unique_together': {('rule', 'user')},
            },
        ),
        migrations.CreateModel(
            name='InboundEmailLog',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('message_id', models.CharField(db_index=True, max_length=255)),
                ('conversation_id', models.CharField(blank=True, db_index=True, max_length=255, null=True)),
                ('status', models.CharField(choices=[('received', 'Received'), ('processed', 'Processed'), ('duplicate', 'Duplicate'), ('failed', 'Failed')], db_index=True, default='received', max_length=32)),
                ('result', models.CharField(blank=True, max_length=64, null=True)),
                ('error', models.TextField(blank=True, null=True)),
                ('payload', models.JSONField(blank=True, default=dict)),
                ('received_at', models.DateTimeField(auto_now_add=True)),
                ('processed_at', models.DateTimeField(blank=True, null=True)),
                ('brand', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inbound_email_logs', to='app.brand')),
                ('ticket', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='inbound_email_logs', to='app.ticket')),
            ],
            options={
                'ordering': ['-received_at'],
                'unique_together': {('brand', 'message_id')},
            },
        ),
        migrations.CreateModel(
            name='AutomationExecution',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('event_key', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('rule', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='executions', to='app.automationrule')),
                ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='automation_executions', to='app.ticket')),
            ],
            options={
                'unique_together': {('rule', 'ticket', 'event_key')},
            },
        ),
    ]
