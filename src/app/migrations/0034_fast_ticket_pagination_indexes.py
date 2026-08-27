from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0033_performance_indexes'),
    ]

    operations = [
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'merged_into', 'updated_at'],
                name='app_ticket_live_updated_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'merged_into', 'status', 'updated_at'],
                name='app_ticket_live_status_upd',
            ),
        ),
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'merged_into', 'assignee', 'updated_at'],
                name='app_ticket_live_assignee_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'merged_into', 'requester', 'updated_at'],
                name='app_ticket_live_requester_idx',
            ),
        ),
        migrations.AddIndex(
            model_name='comment',
            index=models.Index(
                fields=['ticket', 'id'],
                name='app_comment_ticket_id_idx',
            ),
        ),
    ]
