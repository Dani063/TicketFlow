from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0024_macro'),
    ]

    operations = [
        migrations.AddField(
            model_name='macro',
            name='zendesk_id',
            field=models.BigIntegerField(blank=True, db_index=True, null=True, unique=True),
        ),
    ]
