from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [('app', '0070_productline_cognitia_rightfax')]
    operations = [
        migrations.AddField(
            model_name='ticket', name='deleted_at',
            field=models.DateTimeField(blank=True, db_index=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket', name='deleted_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='deleted_tickets', to=settings.AUTH_USER_MODEL),
        ),
    ]
