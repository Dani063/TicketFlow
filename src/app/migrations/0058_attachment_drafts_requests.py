from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('app', '0057_reporting_event_lookup_index')]

    operations = [
        migrations.CreateModel(
            name='AttachmentDraft',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('key', models.UUIDField()),
                ('published_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('ticket', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='app.ticket')),
                ('uploaded_by', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('uploaded_by', 'key'), name='attachment_draft_owner_key')]},
        ),
        migrations.AddField(
            model_name='attachment', name='draft',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.PROTECT, to='app.attachmentdraft'),
        ),
        migrations.AddIndex(
            model_name='attachment',
            index=models.Index(fields=['uploaded_by', 'ticket', 'comment'], name='attachment_pending_owner'),
        ),
        migrations.CreateModel(
            name='AttachmentRequest',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('operation', models.CharField(max_length=16)),
                ('key', models.UUIDField()),
                ('fingerprint', models.CharField(max_length=64)),
                ('result', models.JSONField(default=dict)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, to=settings.AUTH_USER_MODEL)),
                ('attachment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='app.attachment')),
                ('comment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='app.comment')),
                ('ticket', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='app.ticket')),
            ],
            options={'constraints': [models.UniqueConstraint(fields=('actor', 'operation', 'key'), name='attachment_request_actor_key')]},
        ),
    ]
