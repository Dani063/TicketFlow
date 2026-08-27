from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0018_alter_ticket_priority'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='assigned_group',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=models.deletion.SET_NULL,
                related_name='tickets',
                to='app.group',
            ),
        ),
    ]
