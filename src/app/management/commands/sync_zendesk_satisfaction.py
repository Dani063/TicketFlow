"""
Importa/sincroniza valoraciones de satisfacción de Zendesk en TicketFlow.

Uso:
    python manage.py sync_zendesk_satisfaction
    python manage.py sync_zendesk_satisfaction --since 30
    python manage.py sync_zendesk_satisfaction --all
"""

import base64
import time

import requests
from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.utils.dateparse import parse_datetime

from app.models import SatisfactionRating, Ticket, User


class ZendeskClient:
    def __init__(self):
        subdomain = getattr(settings, "ZENDESK_SUBDOMAIN", "")
        email     = getattr(settings, "ZENDESK_EMAIL", "")
        token     = getattr(settings, "ZENDESK_API_TOKEN", "")
        if not (subdomain and email and token):
            raise CommandError(
                "Faltan credenciales de Zendesk. Define ZENDESK_SUBDOMAIN, "
                "ZENDESK_EMAIL y ZENDESK_API_TOKEN en el .env."
            )
        self.base = f"https://{subdomain}.zendesk.com/api/v2"
        creds = f"{email}/token:{token}".encode()
        self.headers = {
            "Authorization": "Basic " + base64.b64encode(creds).decode(),
            "Content-Type": "application/json",
        }

    def get(self, path, params=None):
        url = self.base + path
        resp = requests.get(url, headers=self.headers, params=params, timeout=30)
        if resp.status_code == 401:
            raise CommandError("Zendesk rechazó las credenciales (401).")
        if resp.status_code == 429:
            retry = int(resp.headers.get("Retry-After", 10))
            time.sleep(retry)
            return self.get(path, params)
        resp.raise_for_status()
        return resp.json()


def _parse_dt(value):
    if not value:
        return None
    try:
        return parse_datetime(value)
    except Exception:
        return None


class Command(BaseCommand):
    help = "Sincroniza valoraciones de satisfacción desde Zendesk"

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            type=int,
            default=90,
            metavar="DAYS",
            help="Importar valoraciones de los últimos N días (default: 90)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Importar todas las valoraciones sin límite de fecha",
        )

    def handle(self, *args, **options):
        client = ZendeskClient()
        created = updated = skipped = 0

        # Precargar mapa zendesk_id → ticket local
        ticket_map = dict(
            Ticket.objects.exclude(zendesk_id__isnull=True)
            .values_list("zendesk_id", "id")
        )
        self.stdout.write(f"  Mapa local: {len(ticket_map)} tickets con zendesk_id")

        params = {"per_page": 100, "sort_order": "desc"}
        if not options["all"]:
            import datetime
            cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=options["since"])
            params["start_time"] = int(cutoff.timestamp())

        page = 1
        while True:
            params["page"] = page
            data = client.get("/satisfaction_ratings.json", params=params)
            ratings = data.get("satisfaction_ratings", [])
            if not ratings:
                break

            for zr in ratings:
                zid           = zr["id"]
                score         = zr.get("score", "")
                ztid          = zr.get("ticket_id")
                comment       = (zr.get("comment") or "").strip() or None
                reason        = (zr.get("reason") or "").strip() or None
                req_zid       = zr.get("requester_id")
                ass_zid       = zr.get("assignee_id")
                created_at    = _parse_dt(zr.get("created_at"))
                updated_at    = _parse_dt(zr.get("updated_at"))

                if not ztid:
                    skipped += 1
                    continue

                local_ticket_id = ticket_map.get(ztid)
                requester = User.objects.filter(zendesk_id=req_zid).first() if req_zid else None
                assignee  = User.objects.filter(zendesk_id=ass_zid).first() if ass_zid else None

                _, new = SatisfactionRating.objects.update_or_create(
                    zendesk_id=zid,
                    defaults={
                        "ticket_id":  local_ticket_id,
                        "score":      score,
                        "comment":    comment,
                        "reason":     reason,
                        "requester":  requester,
                        "assignee":   assignee,
                        "created_at": created_at,
                        "updated_at": updated_at,
                    },
                )
                if new:
                    created += 1
                    if local_ticket_id:
                        self.stdout.write(f"  + [{score}] ticket #{ztid}")
                else:
                    updated += 1

            if not data.get("next_page"):
                break
            page += 1

        self.stdout.write(self.style.SUCCESS(
            f"Sync completo: {created} creadas, {updated} actualizadas"
            + (f", {skipped} sin ticket_id" if skipped else "") + "."
        ))
