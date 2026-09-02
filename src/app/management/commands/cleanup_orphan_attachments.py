"""Elimina adjuntos subidos que nunca se asociaron a una respuesta."""

from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from app.models import Attachment
from app.services.attachments import AttachmentService


class Command(BaseCommand):
    help = "Elimina del almacenamiento y de la base de datos los adjuntos huérfanos"

    def add_arguments(self, parser):
        parser.add_argument(
            "--hours",
            type=int,
            default=getattr(settings, "ATTACHMENT_ORPHAN_MAX_AGE_HOURS", 24),
            help="Edad mínima en horas (por defecto, ATTACHMENT_ORPHAN_MAX_AGE_HOURS)",
        )
        parser.add_argument("--dry-run", action="store_true", help="Solo cuenta los candidatos")

    def handle(self, *args, **options):
        cutoff = timezone.now() - timedelta(hours=max(options["hours"], 1))
        candidates = Attachment.objects.filter(
            comment__isnull=True,
            uploaded_at__lt=cutoff,
        )
        if options["dry_run"]:
            self.stdout.write(f"Adjuntos huérfanos candidatos: {candidates.count()}")
            return

        totals = {"deleted": 0, "failed": 0}
        while True:
            result = AttachmentService.cleanup_orphans(cutoff=cutoff, batch_size=500)
            totals["deleted"] += result["deleted"]
            totals["failed"] += result["failed"]
            if result["deleted"] < 500:
                break
        self.stdout.write(self.style.SUCCESS(
            f"Adjuntos eliminados: {totals['deleted']}; fallidos: {totals['failed']}"
        ))
