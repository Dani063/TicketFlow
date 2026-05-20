import os
import subprocess
import sys

from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Start Celery worker"

    def add_arguments(self, parser):
        parser.add_argument("--concurrency", type=int, default=2)
        parser.add_argument("--loglevel", default="info")

    def handle(self, *args, **options):
        queue_url = os.getenv("CELERY_SQS_QUEUE_URL", "")
        broker = os.getenv("CELERY_BROKER_URL", "sqs://")
        if broker == "sqs://" and not queue_url:
            self.stderr.write("ERROR: CELERY_SQS_QUEUE_URL no definida.")
            sys.exit(1)
        cmd = [
            "celery", "-A", "TicketFlow", "worker",
            "-l", options["loglevel"],
            "--concurrency", str(options["concurrency"]),
        ]
        os.execvp("celery", cmd)
