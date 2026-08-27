from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0016_alter_ticket_updated_at'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='zendesk_id',
            field=models.BigIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
        migrations.AddField(
            model_name='comment',
            name='zendesk_id',
            field=models.BigIntegerField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='user',
            name='zendesk_id',
            field=models.BigIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
    ]
