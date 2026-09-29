"""Comprueba el Job de migraciones sin cambiar el esquema ni los datos de negocio."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0063_restore_media_only_help_articles"),
    ]

    # Django solo registra que se ha aplicado en django_migrations.
    operations = []
