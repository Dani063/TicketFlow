from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0022_user_profile_fields'),
    ]

    operations = [
        migrations.CreateModel(
            name='TicketEvent',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('field_name', models.CharField(max_length=100)),
                ('old_value', models.TextField(blank=True, null=True)),
                ('new_value', models.TextField(blank=True, null=True)),
                ('created_at', models.DateTimeField()),
                ('zendesk_event_id', models.BigIntegerField(blank=True, db_index=True, null=True, unique=True)),
                ('actor', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='ticket_events',
                    to='app.user',
                )),
                ('ticket', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='events',
                    to='app.ticket',
                )),
            ],
            options={'ordering': ['created_at']},
        ),
    ]
