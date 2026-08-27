from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0017_zendesk_id_fields'),
    ]

    operations = [
        migrations.AlterField(
            model_name='ticket',
            name='priority',
            field=models.CharField(
                blank=True,
                choices=[('low', 'Low'), ('normal', 'Normal'), ('high', 'High'), ('urgent', 'Urgent')],
                max_length=255,
                null=True,
            ),
        ),
    ]
