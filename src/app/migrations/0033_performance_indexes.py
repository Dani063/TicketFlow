from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0032_ticket_is_deleted'),
    ]

    operations = [
        # Composite index on Comment(ticket_id, created_at):
        # Eliminates the 22s filesort in last-comment lookups.
        # The FK-only index on ticket_id leaves ORDER BY created_at as a full sort.
        migrations.AddIndex(
            model_name='comment',
            index=models.Index(
                fields=['ticket', 'created_at'],
                name='app_comment_ticket_created_idx',
            ),
        ),

        # Composite index on Ticket(is_deleted, updated_at):
        # Base queryset is always filtered by is_deleted=False; ORDER BY updated_at DESC
        # is the default sort for every paginated view. Without this, MySQL does a
        # filesort over ~35k rows on every page request.
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'updated_at'],
                name='app_ticket_deleted_updated_idx',
            ),
        ),

        # Composite index on Ticket(is_deleted, status):
        # Sidebar computes ~15 status-based COUNTs. Each needs (is_deleted, status)
        # to avoid full scans. After consolidation into one aggregate() call, MySQL
        # still needs this index to quickly locate rows by status.
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'status'],
                name='app_ticket_deleted_status_idx',
            ),
        ),

        # Composite index on Ticket(is_deleted, channel):
        # Twitter/channel filters use channel= equality; same pattern as status.
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'channel'],
                name='app_ticket_deleted_channel_idx',
            ),
        ),

        # Composite index on Ticket(is_deleted, service):
        # Comunycarse/EcomFax/Recordia SGSD service filters.
        migrations.AddIndex(
            model_name='ticket',
            index=models.Index(
                fields=['is_deleted', 'service'],
                name='app_ticket_deleted_service_idx',
            ),
        ),
    ]
