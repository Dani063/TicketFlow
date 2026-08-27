from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0019_ticket_assigned_group'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='security_related',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='monitoring',
            field=models.BooleanField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='approval_status',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='resolution_type',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='required_tasks',
            field=models.CharField(blank=True, max_length=255, null=True),
        ),
        migrations.CreateModel(
            name='ZendeskFieldMap',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('zendesk_field_id', models.BigIntegerField(db_index=True, unique=True)),
                ('zendesk_title', models.CharField(max_length=255)),
                ('zendesk_type', models.CharField(max_length=50)),
                ('ticketflow_attr', models.CharField(blank=True, max_length=100, null=True)),
                ('active', models.BooleanField(default=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
        ),
    ]
