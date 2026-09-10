"""Une el historial de STA con DEV sin renumerar migraciones ya aplicadas."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0053_seed_agent_reply_templates"),
        ("app", "0058_attachment_drafts_requests"),
    ]

    operations = []
