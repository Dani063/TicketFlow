"""Rol «Solo lectura» (#21378).

Ve lo mismo que un agente pero no modifica nada. Los permisos dependen del
nombre (app/permissions.py), así que se siembra igual en todos los entornos y
el panel de administración no deja renombrarlo ni borrarlo.
"""

from django.db import migrations


ROLE_NAME = "Solo lectura"
DESCRIPTION = "Consulta tickets, clientes y reporting como un agente, sin poder modificar nada."


def seed(apps, schema_editor):
    Role = apps.get_model("app", "Role")
    if not Role.objects.filter(role_name__iexact=ROLE_NAME).exists():
        Role.objects.create(role_name=ROLE_NAME, description=DESCRIPTION)


def unseed(apps, schema_editor):
    Role = apps.get_model("app", "Role")
    User = apps.get_model("app", "User")
    # Solo si nadie lo usa: borrar un rol asignado dejaría usuarios sin rol.
    if not User.objects.filter(role__role_name__iexact=ROLE_NAME).exists():
        Role.objects.filter(role_name__iexact=ROLE_NAME).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0073_ticket_presence"),
    ]

    operations = [
        migrations.RunPython(seed, unseed),
    ]
