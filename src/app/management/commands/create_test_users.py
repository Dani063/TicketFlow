"""
Crea grupos base, roles y usuarios de prueba para desarrollo.

Uso:
    python src/main.py create_test_users
    python src/main.py create_test_users --assign-existing   # asigna usuarios ya creados al grupo Agents
"""

from django.core.management.base import BaseCommand

from app.models import Group, Role, User


END_USERS_GROUP = "End users"
AGENTS_GROUP    = "Agents"
END_USER_ROLE   = "End user"
AGENT_ROLE      = "agent"

TEST_END_USERS = [
    {"name": "Ana García",    "email": "ana.garcia@test.local"},
    {"name": "Carlos López",  "email": "carlos.lopez@test.local"},
    {"name": "María Ruiz",    "email": "maria.ruiz@test.local"},
    {"name": "Pedro Sánchez", "email": "pedro.sanchez@test.local"},
]

TEST_AGENTS = [
    {"name": "Laura Martín",  "email": "laura.martin@test.local"},
]

DEFAULT_PASSWORD = "Test1234!"


class Command(BaseCommand):
    help = "Crea grupos base y usuarios de prueba para desarrollo local."

    def add_arguments(self, parser):
        parser.add_argument(
            "--assign-existing",
            action="store_true",
            help="Asigna los usuarios ya existentes (sin grupo) al grupo Agents.",
        )

    def handle(self, *args, **options):
        # --- Grupos ---
        end_users_group, _ = Group.objects.get_or_create(group_name=END_USERS_GROUP)
        agents_group, _    = Group.objects.get_or_create(group_name=AGENTS_GROUP)
        self.stdout.write(f"  Grupos: '{END_USERS_GROUP}' (id={end_users_group.pk}), '{AGENTS_GROUP}' (id={agents_group.pk})")

        # --- Roles ---
        end_user_role, _ = Role.objects.get_or_create(role_name=END_USER_ROLE)
        agent_role, _    = Role.objects.get_or_create(role_name=AGENT_ROLE)
        self.stdout.write(f"  Roles: '{END_USER_ROLE}' (id={end_user_role.pk}), '{AGENT_ROLE}' (id={agent_role.pk})")

        # --- Usuarios existentes sin grupo → Agents ---
        if options["assign_existing"]:
            updated = (
                User.objects
                .filter(group__isnull=True)
                .update(group=agents_group, role=agent_role)
            )
            self.stdout.write(self.style.WARNING(
                f"  {updated} usuarios existentes asignados al grupo '{AGENTS_GROUP}'."
            ))

        # --- End users de prueba ---
        self.stdout.write("\nEnd users:")
        for data in TEST_END_USERS:
            user, created = User.objects.get_or_create(
                email=data["email"],
                defaults={
                    "name":  data["name"],
                    "group": end_users_group,
                    "role":  end_user_role,
                },
            )
            if created:
                user.set_password(DEFAULT_PASSWORD)
                user.save()
                self.stdout.write(self.style.SUCCESS(f"  + {user.name} <{user.email}>"))
            else:
                self.stdout.write(f"  = {user.name} <{user.email}> (ya existía)")

        # --- Agentes de prueba extra ---
        self.stdout.write("\nAgentes:")
        for data in TEST_AGENTS:
            user, created = User.objects.get_or_create(
                email=data["email"],
                defaults={
                    "name":  data["name"],
                    "group": agents_group,
                    "role":  agent_role,
                },
            )
            if created:
                user.set_password(DEFAULT_PASSWORD)
                user.save()
                self.stdout.write(self.style.SUCCESS(f"  + {user.name} <{user.email}>"))
            else:
                self.stdout.write(f"  = {user.name} <{user.email}> (ya existía)")

        self.stdout.write(self.style.SUCCESS(
            f"\nListo. Contraseña de todos los usuarios nuevos: {DEFAULT_PASSWORD}"
        ))
        self.stdout.write(
            f"  End users group id={end_users_group.pk} | Agents group id={agents_group.pk}\n"
            "  Actualiza views.py si los IDs de grupo no son 1 y 2."
        )
