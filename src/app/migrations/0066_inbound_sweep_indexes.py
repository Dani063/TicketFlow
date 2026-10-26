"""Índices del barrido por ventana de buzones M365.

Separados de las columnas para que un bloqueo de metadatos al crear índices
no deje a medias el alta de columnas. En MySQL se acota la espera del bloqueo:
si hay una transacción larga sobre la tabla, la migración falla en 10 s en
lugar de encolar detrás de ella todas las escrituras de la ingesta.
"""

from django.db import migrations, models


def _limit_lock_wait(apps, schema_editor):
    # Por un cursor normal y fuera de transacción (atomic=False abajo): en MySQL,
    # schema_editor.execute() dentro del atomic de un RunPython lanza
    # TransactionManagementError. Es la misma conexión que crea los índices.
    if schema_editor.connection.vendor == 'mysql':
        with schema_editor.connection.cursor() as cursor:
            cursor.execute('SET SESSION lock_wait_timeout = 10')


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0065_inbound_sweep_fields'),
    ]

    operations = [
        migrations.RunPython(_limit_lock_wait, migrations.RunPython.noop, atomic=False),
        migrations.AddIndex(
            model_name='inboundemaillog',
            index=models.Index(fields=['status', 'next_attempt_at'], name='app_inbound_status_next_idx'),
        ),
        migrations.AddIndex(
            model_name='inboundemaillog',
            index=models.Index(fields=['graph_received_at'], name='app_inbound_graph_recv_idx'),
        ),
        migrations.AddIndex(
            model_name='inboundemaillog',
            index=models.Index(fields=['internet_message_key'], name='app_inbound_imsg_key_idx'),
        ),
        migrations.AddIndex(
            model_name='inboundemaillog',
            index=models.Index(fields=['received_at'], name='app_inbound_received_idx'),
        ),
        migrations.AddConstraint(
            model_name='inboundemaillog',
            constraint=models.UniqueConstraint(fields=('folder_key', 'message_key'), name='app_inbound_folder_msg_uniq'),
        ),
    ]
