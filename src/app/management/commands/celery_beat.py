import os
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Start Celery beat scheduler"

    def add_arguments(self, parser):
        parser.add_argument("--loglevel", default="info")
        parser.add_argument("--schedule", default="/tmp/celerybeat-schedule")

    def handle(self, *args, **options):
        queue_url = os.getenv("CELERY_SQS_QUEUE_URL", "")
        broker = os.getenv("CELERY_BROKER_URL", "sqs://")
        if broker == "sqs://" and not queue_url:
            self.stderr.write("ERROR: CELERY_SQS_QUEUE_URL no definida.")
            sys.exit(1)
        cmd = [
            "celery", "-A", "TicketFlow", "beat",
            "-l", options["loglevel"],
            "-s", options["schedule"],
        ]
        os.execvp("celery", cmd)
