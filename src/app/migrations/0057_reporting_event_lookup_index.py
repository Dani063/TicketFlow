from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0056_seed_agent_reply_templates"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="ticketevent",
            index=models.Index(
                fields=["ticket", "field_name", "created_at"],
                name="app_evt_tkt_field_time",
            ),
        ),
    ]
