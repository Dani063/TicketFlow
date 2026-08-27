import os

from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


MIGRATION_LOCK_NAME = "ticketflow:start_web:migrations"
MIGRATION_LOCK_TIMEOUT_SECONDS = 300


class Command(BaseCommand):
    help = "Start Gunicorn web server"

    def add_arguments(self, parser):
        parser.add_argument("--workers", type=int, default=3)
        parser.add_argument("--threads", type=int, default=1)
        parser.add_argument("--timeout", type=int, default=60)
        parser.add_argument("--bind", default="0.0.0.0:8000")
        parser.add_argument("--loglevel", default="info")

    def handle(self, *args, **options):
        self._apply_migrations()

        # El runtime corporativo descarga el artefacto versionado en
        # /app/service/<servicio>.<version> y añade allí el .env. Eso hace que
        # PROJECT_ROOT/STATIC_ROOT apunten a esa ruta efectiva, no a la usada
        # durante la construcción de la imagen. Recopilar aquí garantiza que
        # WhiteNoise encuentre los estáticos en cualquier empaquetado/runtime.
        self.stdout.write("Collecting static files...")
        call_command("collectstatic", interactive=False, verbosity=1)

        cmd = [
            "gunicorn", "TicketFlow.wsgi:application",
            "--bind",     options["bind"],
            "--workers",  str(options["workers"]),
            "--threads",  str(options["threads"]),
            "--timeout",  str(options["timeout"]),
            "--log-level", options["loglevel"],
            "--access-logfile", "-",
            "--error-logfile",  "-",
            "--logger-class", "TicketFlow.gunicorn_logging.JsonLogger",
        ]
        os.execvp("gunicorn", cmd)

    def _apply_migrations(self):
        """Apply pending migrations once, even if several web pods start together."""
        self.stdout.write("Applying database migrations...")

        if connection.vendor != "mysql":
            call_command("migrate", interactive=False, verbosity=1)
            return

        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT GET_LOCK(%s, %s)",
                [MIGRATION_LOCK_NAME, MIGRATION_LOCK_TIMEOUT_SECONDS],
            )
            acquired = cursor.fetchone()[0]

        if acquired != 1:
            connection.close()
            raise CommandError(
                "Could not acquire the database migration lock within "
                f"{MIGRATION_LOCK_TIMEOUT_SECONDS} seconds."
            )

        try:
            call_command("migrate", interactive=False, verbosity=1)
        finally:
            try:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT RELEASE_LOCK(%s)", [MIGRATION_LOCK_NAME])
            finally:
                connection.close()
