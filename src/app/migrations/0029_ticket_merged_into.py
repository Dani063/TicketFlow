import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0028_brand_organization_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='merged_into',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='merged_tickets',
                to='app.ticket',
            ),
        ),
    ]
