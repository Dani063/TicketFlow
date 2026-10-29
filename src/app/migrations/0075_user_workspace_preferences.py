from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('app', '0074_seed_readonly_role')]
    operations = [migrations.AddField(
        model_name='user', name='workspace_preferences',
        field=models.JSONField(default=dict, blank=True),
    )]
