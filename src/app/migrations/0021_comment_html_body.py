from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0020_custom_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='comment',
            name='html_body',
            field=models.TextField(blank=True, null=True),
        ),
    ]
