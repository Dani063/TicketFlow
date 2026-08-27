from django.db import migrations


RULE_NAME = "Default inbound"
# Reproduce el pool historico hardcodeado en tasks.py antes del refactor.
TARGET_NAMES = ("Laura Moccia", "Guillermo Soret", "Jose Maria")


def seed_rule(apps, schema_editor):
    AssignmentRule = apps.get_model("app", "AssignmentRule")
    AssignmentRuleMember = apps.get_model("app", "AssignmentRuleMember")
    User = apps.get_model("app", "User")

    if AssignmentRule.objects.filter(name=RULE_NAME).exists():
        return

    members = []
    for name in TARGET_NAMES:
        user = User.objects.filter(name__icontains=name, is_active=True).first()
        if user:
            members.append(user)

    # Sin los usuarios objetivo no sembramos nada: el servicio caera en su
    # fallback (balanceo entre agentes) y un admin puede crear la regla a mano.
    if not members:
        return

    rule = AssignmentRule.objects.create(name=RULE_NAME, active=True)
    for user in members:
        AssignmentRuleMember.objects.get_or_create(
            rule=rule,
            user=user,
            defaults={"weight": 1, "capacity": None, "active": True},
        )


def remove_rule(apps, schema_editor):
    AssignmentRule = apps.get_model("app", "AssignmentRule")
    AssignmentRule.objects.filter(name=RULE_NAME).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0037_ticket_first_responded_at"),
    ]

    operations = [
        migrations.RunPython(seed_rule, remove_rule),
    ]
