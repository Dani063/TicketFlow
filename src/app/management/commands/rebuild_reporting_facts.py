"""Backfill de las tablas de hechos de /reporting/.

Se ejecuta una vez tras desplegar la migracion 0059, y despues solo si hay que
regenerar el derivado (por ejemplo tras una importacion masiva). El dia a dia lo
mantiene la tarea `app.tasks.refresh_reporting_facts`.
"""

from django.core.management.base import BaseCommand

from app.models import Ticket, TicketFact
from app.services.reporting_facts import (
    DEFAULT_BATCH_SIZE, rebuild_all_facts, refresh_stale_facts,
)


class Command(BaseCommand):
    help = "Calcula TicketFact y TicketStatusInterval para el reporting"

    def add_arguments(self, parser):
        parser.add_argument(
            "--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
            help="Tickets por lote (techo de memoria del proceso).",
        )
        parser.add_argument(
            "--stale-only", action="store_true",
            help="Recalcula solo los tickets rancios en vez del historico entero.",
        )

    def handle(self, *args, **options):
        batch_size = options["batch_size"]

        if options["stale_only"]:
            processed = refresh_stale_facts(batch_size=batch_size)
            self.stdout.write(self.style.SUCCESS(
                f"Hechos actualizados para {processed} tickets rancios."
            ))
            return

        total = Ticket.objects.count()
        self.stdout.write(f"Reconstruyendo hechos de {total} tickets (lotes de {batch_size})…")

        def progress(done, overall):
            # Un backfill del historico completo dura minutos: sin traza parece colgado.
            self.stdout.write(f"  {done}/{overall} tickets", ending="\r")

        processed = rebuild_all_facts(batch_size=batch_size, progress=progress)
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"Listo: {processed} tickets procesados, {TicketFact.objects.count()} filas de hechos."
        ))
