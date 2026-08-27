from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0030_email_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='email_conversation_id',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
    ]
