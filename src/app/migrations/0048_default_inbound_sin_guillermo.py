from django.db import migrations


RULE_NAME = "Default inbound"

# El reparto automatico pasa a ser solo Laura y Jose Maria: Guillermo Soret ya
# no es agente de reparto. Se identifica por email porque los nombres llevan
# acentos y el seed de 0038 (`name__icontains="Jose Maria"`) no casaba con
# «Jose Maria Sanchez» con tildes: en la BBDD de desarrollo la regla no llego a
# crearse y todo caia al fallback de AssignmentService, que reparte entre TODOS
# los usuarios con rol de agente (Guillermo incluido, y tambien los admin).
# Por eso esta migracion no solo desactiva a Guillermo: garantiza la regla.
IN_EMAILS = (
    "mocparla@comunycarse.com",   # Laura Moccia Parada
    "sangaljo@comunycarse.com",   # Jose Maria Sanchez
)


def ensure_rule(apps, schema_editor):
    AssignmentRule = apps.get_model("app", "AssignmentRule")
    AssignmentRuleMember = apps.get_model("app", "AssignmentRuleMember")
    User = apps.get_model("app", "User")

    targets = list(User.objects.filter(email__in=IN_EMAILS, is_active=True))
    if not targets:
        # Sin los usuarios objetivo no tocamos nada: preferimos el fallback
        # actual a dejar una regla vacia que no asigna a nadie. Un admin puede
        # crear la regla desde la pestana «Asignacion» del panel.
        return

    rule = AssignmentRule.objects.filter(name=RULE_NAME).first()
    if rule is None:
        rule = AssignmentRule.objects.create(name=RULE_NAME, active=True)
    elif not rule.active:
        rule.active = True
        rule.save(update_fields=["active"])

    for user in targets:
        member, created = AssignmentRuleMember.objects.get_or_create(
            rule=rule,
            user=user,
            defaults={"weight": 1, "capacity": None, "active": True},
        )
        if not created and not member.active:
            member.active = True
            member.save(update_fields=["active"])

    # Cualquier otro miembro (Guillermo) deja de recibir tickets. Se desactiva
    # en vez de borrarse para conservar su peso/capacidad y poder revertirlo.
    AssignmentRuleMember.objects.filter(rule=rule).exclude(user__email__in=IN_EMAILS).update(active=False)


def undo(apps, schema_editor):
    # No se puede reconstruir el estado exacto anterior; se reactivan todos los
    # miembros de la regla, que es el reparto a tres de antes de este cambio.
    AssignmentRuleMember = apps.get_model("app", "AssignmentRuleMember")
    AssignmentRuleMember.objects.filter(rule__name=RULE_NAME).update(active=True)


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0047_seed_satisfaction_survey"),
    ]

    operations = [
        migrations.RunPython(ensure_rule, undo),
    ]
