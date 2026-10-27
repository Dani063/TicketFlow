"""Estado on_hold (#21384): reloj de cierre automático y pausa del SLA.

Los dos campos son nulos para que los pods con la imagen anterior sigan
insertando tickets durante el despliegue; cambiar las choices no genera DDL.
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0067_inbound_ids_case_sensitive'),
    ]

    operations = [
        migrations.AddField(
            model_name='ticket',
            name='on_hold_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='ticket',
            name='sla_paused_seconds',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
        migrations.AlterField(
            model_name='ticket',
            name='status',
            field=models.CharField(choices=[('open', 'Open'), ('pending', 'Pending'), ('closed', 'Closed'), ('resolved', 'Resolved'), ('on_hold', 'On-hold')], max_length=255),
        ),
    ]
