from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('app', '0072_attachment_inline')]
    operations = [migrations.CreateModel(
        name='TicketPresence',
        fields=[
            ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
            ('last_seen', models.DateTimeField(db_index=True)),
            ('composing', models.BooleanField(default=False)),
            ('ticket', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='presences', to='app.ticket')),
            ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ticket_presences', to=settings.AUTH_USER_MODEL)),
        ],
        options={
            'constraints': [models.UniqueConstraint(fields=['ticket', 'user'], name='ticket_presence_user_unique')],
            'indexes': [models.Index(fields=['ticket', 'last_seen'], name='ticket_presence_seen_idx')],
        },
    )]
