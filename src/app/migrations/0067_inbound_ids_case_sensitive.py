"""Ids de Graph en InboundEmailLog con colación binaria (solo MySQL).

Los ids de Graph distinguen mayúsculas y son base64 consecutivos, pero
``message_id`` y ``conversation_id`` usaban ``utf8mb4_unicode_ci``. Un correo
nuevo chocaba con otro antiguo que solo difería en una letra (``…8zOiAAA=``
frente a ``…8zOIAAA=``): con el delta se daba por ingerido sin rastro, y con el
barrido la unicidad ``(brand, message_id)`` impedía registrarlo. En PRO dejó de
entrar correo el 05/10/2026 desde las 07:00 UTC.

Solo se toca esta tabla (unos 200 MB): reconstruir ``app_comment`` (1,7 GB)
bloquearía los comentarios varios minutos, así que para ``Ticket`` y ``Comment``
la comparación exacta se hace en Python (``email_ingestion.exact_match``).

No cambia el estado de los modelos: SQLite no conoce esta colación. Un
``AlterField`` futuro sobre estas columnas en MySQL la devolvería a la colación
de la tabla; si se toca el campo, repetir este ALTER.
"""

from django.db import migrations

COLUMNS = (
    ('message_id', 'varchar(255)', 'NOT NULL'),
    ('conversation_id', 'varchar(255)', 'NULL'),
)


def _to_binary(apps, schema_editor):
    connection = schema_editor.connection
    if connection.vendor != 'mysql':
        return
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT COLUMN_NAME, COLLATION_NAME FROM information_schema.COLUMNS "
            "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'app_inboundemaillog' "
            "AND COLUMN_NAME IN ('message_id', 'conversation_id')"
        )
        current = dict(cursor.fetchall())
        pending = [col for col in COLUMNS if current.get(col[0]) != 'utf8mb4_bin']
        if not pending:
            return
        cursor.execute('SET SESSION lock_wait_timeout = 10')
        clauses = ', '.join(
            f'MODIFY {name} {kind} CHARACTER SET utf8mb4 COLLATE utf8mb4_bin {null}'
            for name, kind, null in pending
        )
        cursor.execute(f'ALTER TABLE app_inboundemaillog {clauses}')


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0066_inbound_sweep_indexes'),
    ]

    operations = [
        migrations.RunPython(_to_binary, migrations.RunPython.noop, atomic=False),
    ]
