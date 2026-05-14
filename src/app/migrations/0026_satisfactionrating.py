from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0025_macro_zendesk_id'),
    ]

    operations = [
        migrations.CreateModel(
            name='SatisfactionRating',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('zendesk_id', models.BigIntegerField(db_index=True, unique=True)),
                ('zendesk_ticket_id', models.BigIntegerField(db_index=True)),
                ('score', models.CharField(
                    choices=[('offered', 'Offered'), ('unoffered', 'Unoffered'), ('good', 'Good'), ('bad', 'Bad')],
                    max_length=50,
                )),
                ('comment', models.TextField(blank=True, null=True)),
                ('reason', models.CharField(blank=True, max_length=255, null=True)),
                ('requester_zendesk_id', models.BigIntegerField(blank=True, null=True)),
                ('assignee_zendesk_id', models.BigIntegerField(blank=True, null=True)),
                ('created_at', models.DateTimeField()),
                ('updated_at', models.DateTimeField(blank=True, null=True)),
                ('ticket', models.ForeignKey(
                    blank=True, null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='satisfaction_ratings',
                    to='app.ticket',
                )),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
    ]
