"""
Importa/sincroniza macros de Zendesk en TicketFlow.

Uso:
    python manage.py sync_zendesk_macros
    python manage.py sync_zendesk_macros --deactivate-missing
"""

import base64
import re

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.models import Macro

# Campos de acción de Zendesk que mapeamos a nuestra estructura
_ACTION_MAP = {
    "status":            "status",
    "priority":          "priority",
    "assignee_id":       "assignee_id",
    "comment_value":     "comment",
    "comment_value_html": "_comment_html",  # fallback si no hay plain
}

_STRIP_TAGS = re.compile(r"<[^>]+>")


def _strip_html(html: str) -> str:
    return _STRIP_TAGS.sub("", html or "").strip()


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
        resp.raise_for_status()
        return resp.json()

    def macros_page(self, page=1):
        return self.get("/macros.json", params={"active": "true", "page": page, "per_page": 100})


def _parse_actions(raw_actions: list) -> dict:
    actions = {}
    has_plain_comment = any(a.get("field") == "comment_value" for a in raw_actions)

    for action in raw_actions:
        field = action.get("field")
        value = action.get("value")

        if field == "status" and value:
            # Zendesk "solved" → TicketFlow "resolved"
            actions["status"] = "resolved" if value == "solved" else value
        elif field == "priority" and value:
            actions["priority"] = value
        elif field == "assignee_id" and value:
            try:
                actions["assignee_id"] = int(value)
            except (TypeError, ValueError):
                pass
        elif field == "comment_value" and value:
            actions["comment"] = value
        elif field == "comment_value_html" and value and not has_plain_comment:
            actions["comment"] = _strip_html(value)
        # group_id, tags, etc. → ignored for now

    return actions


class Command(BaseCommand):
    help = "Sincroniza macros activas desde Zendesk"

    def add_arguments(self, parser):
        parser.add_argument(
            "--deactivate-missing",
            action="store_true",
            help="Desactiva en TicketFlow las macros que ya no existen/están activas en Zendesk",
        )

    def handle(self, *args, **options):
        client = ZendeskClient()
        created = updated = 0
        seen_ids: set[int] = set()

        page = 1
        while True:
            data = client.macros_page(page)
            macros = data.get("macros", [])
            if not macros:
                break

            for zm in macros:
                zid   = zm["id"]
                name  = (zm.get("title") or "").strip()
                desc  = (zm.get("description") or "").strip()[:500]
                active = zm.get("active", True)
                actions = _parse_actions(zm.get("actions", []))
                seen_ids.add(zid)

                _, new = Macro.objects.update_or_create(
                    zendesk_id=zid,
                    defaults={
                        "name":        name,
                        "description": desc or None,
                        "actions":     actions,
                        "active":      active,
                    },
                )
                if new:
                    created += 1
                    self.stdout.write(f"  + {name}")
                else:
                    updated += 1

            if not data.get("next_page"):
                break
            page += 1

        if options["deactivate_missing"] and seen_ids:
            deactivated = (
                Macro.objects
                .filter(active=True, zendesk_id__isnull=False)
                .exclude(zendesk_id__in=seen_ids)
                .update(active=False)
            )
            if deactivated:
                self.stdout.write(f"  ~ {deactivated} macros desactivadas (ya no en Zendesk)")

        self.stdout.write(self.style.SUCCESS(
            f"Sync completo: {created} creadas, {updated} actualizadas."
        ))
