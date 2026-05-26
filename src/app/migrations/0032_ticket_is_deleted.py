from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0031_ticket_email_conversation_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='is_deleted',
            field=models.BooleanField(db_index=True, default=False),
        ),
    ]
