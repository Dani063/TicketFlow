from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0034_fast_ticket_pagination_indexes'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'merged_into', 'type', 'updated_at'],
                name='app_ticket_live_type_upd',
            ),
        ),
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'merged_into', 'channel', 'updated_at'],
                name='app_ticket_live_channel',
            ),
        ),
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'merged_into', 'service', 'status', 'updated_at'],
                name='app_ticket_live_service',
            ),
        ),
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'merged_into', 'created_at', 'updated_at'],
                name='app_ticket_live_created',
            ),
        ),
    ]
