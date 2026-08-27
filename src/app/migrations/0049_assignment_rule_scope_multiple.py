from django.db import migrations, models


def copy_scope_forward(apps, schema_editor):
    """El grupo/servicio/canal unico de cada regla pasa a ser una coleccion."""
    AssignmentRule = apps.get_model("app", "AssignmentRule")

    for rule in AssignmentRule.objects.all():
        rule.services = [rule.service] if rule.service else []
        rule.channels = [rule.channel] if rule.channel else []
        rule.save(update_fields=["services", "channels"])
        if rule.group_id:
            rule.groups.add(rule.group_id)


def copy_scope_backward(apps, schema_editor):
    """Vuelta atras: solo cabe un valor por dimension, se conserva el primero."""
    AssignmentRule = apps.get_model("app", "AssignmentRule")

    for rule in AssignmentRule.objects.all():
        services = rule.services or []
        channels = rule.channels or []
        rule.service = services[0] if services else None
        rule.channel = channels[0] if channels else None
        first_group = rule.groups.first()
        rule.group = first_group
        rule.save(update_fields=["service", "channel", "group"])


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0048_default_inbound_sin_guillermo"),
    ]

    # Orden deliberado: primero se añaden los campos nuevos, luego se copia el
    # ambito existente y solo despues se retiran los antiguos. La migracion que
    # autogenera Django elimina antes de añadir y perderia el ambito guardado.
    operations = [
        migrations.AddField(
            model_name="assignmentrule",
            name="services",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="assignmentrule",
            name="channels",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="assignmentrule",
            name="groups",
            field=models.ManyToManyField(blank=True, related_name="assignment_rules", to="app.group"),
        ),
        migrations.RunPython(copy_scope_forward, copy_scope_backward),
        migrations.RemoveField(model_name="assignmentrule", name="service"),
        migrations.RemoveField(model_name="assignmentrule", name="channel"),
        migrations.RemoveField(model_name="assignmentrule", name="group"),
    ]
