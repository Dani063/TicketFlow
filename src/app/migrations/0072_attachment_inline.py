from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('app', '0071_ticket_deleted_audit')]
    operations = [
        migrations.AddField(model_name='attachment', name='content_id',
                            field=models.TextField(blank=True, default='', db_default='')),
        migrations.AddField(model_name='attachment', name='is_inline',
                            field=models.BooleanField(default=False, db_default=False)),
    ]
