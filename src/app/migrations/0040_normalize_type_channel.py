# Normaliza valores legacy de Ticket.type y Ticket.channel a la taxonomía canónica.
#
# A fecha de la migración la BBDD ya está limpia (los valores españoles del
# formulario nunca persistieron de forma apreciable), pero el formulario llevaba
# meses ofreciendo "incidencia"/"mejora"/"telefono", así que esto actúa como red
# de seguridad para cualquier fila rezagada. Updates set-based por valor; los
# valores desconocidos se dejan intactos (la clasificación IA los cubrirá).

from django.db import migrations

# Copias congeladas (no importar de app.constants: las migraciones deben ser inmutables).
LEGACY_TYPE_MAP = {
    "incidencia": "incident",
    "consulta": "question",
    "pregunta": "question",
    "mejora": "problem",
    "problema": "problem",
    "tarea": "task",
}

LEGACY_CHANNEL_MAP = {
    "telefono": "phone",
    "teléfono": "phone",
    "voice": "phone",
    "mail": "email",
    "web_form": "web",
    "interno": "internal",
}


def normalize_forward(apps, schema_editor):
    Ticket = apps.get_model("app", "Ticket")
    for legacy, canonical in LEGACY_TYPE_MAP.items():
        Ticket.objects.filter(type__iexact=legacy).update(type=canonical)
    for legacy, canonical in LEGACY_CHANNEL_MAP.items():
        Ticket.objects.filter(channel__iexact=legacy).update(channel=canonical)


def normalize_reverse(apps, schema_editor):
    # Los valores canónicos son un superconjunto válido del estado anterior:
    # revertir el esquema no requiere des-normalizar los datos.
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0039_entity_taxonomy_sla_fields"),
    ]

    operations = [
        migrations.RunPython(normalize_forward, normalize_reverse),
    ]
