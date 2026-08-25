import os

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Start Gunicorn web server"

    def add_arguments(self, parser):
        parser.add_argument("--workers", type=int, default=3)
        parser.add_argument("--threads", type=int, default=1)
        parser.add_argument("--timeout", type=int, default=60)
        parser.add_argument("--bind", default="0.0.0.0:8000")
        parser.add_argument("--loglevel", default="info")

    def handle(self, *args, **options):
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
