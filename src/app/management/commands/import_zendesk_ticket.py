"""
Importa un ticket de Zendesk en TicketFlow.

Uso:
    python manage.py import_zendesk_ticket <zendesk_ticket_id>
    python manage.py import_zendesk_ticket <zendesk_ticket_id> --dry-run
"""

import base64

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_datetime

from app.models import Comment, Role, Ticket, TicketTag, User


# Zendesk -> TicketFlow status mapping
STATUS_MAP = {
    "new": "open",
    "open": "open",
    "pending": "pending",
    "hold": "pending",
    "solved": "resolved",
    "closed": "closed",
}

PRIORITY_MAP = {
    "low": "low",
    "normal": "normal",
    "high": "high",
    "urgent": "urgent",
}


class ZendeskClient:
    """Cliente mínimo para la API REST v2 de Zendesk."""

    def __init__(self, subdomain, email, token):
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
        if resp.status_code == 404:
            raise CommandError(f"No encontrado en Zendesk: {url}")
        if resp.status_code == 401:
            raise CommandError("Zendesk rechazó las credenciales (401). Revisa email + API token.")
        resp.raise_for_status()
        return resp.json()

    def ticket(self, ticket_id):
        return self.get(f"/tickets/{ticket_id}.json")["ticket"]

    def comments(self, ticket_id):
        items = []
        page = 1
        while True:
            data = self.get(
                f"/tickets/{ticket_id}/comments.json",
                params={"page": page, "per_page": 100},
            )
            items.extend(data.get("comments", []))
            if not data.get("next_page"):
                break
            page += 1
        return items

    def user(self, user_id):
        return self.get(f"/users/{user_id}.json")["user"]


def _get_or_create_user(client, zendesk_user_id, cache):
    """Crea/recupera el User local equivalente al usuario de Zendesk.

    Estrategia:
      1. Si ya tenemos un User con ese zendesk_id -> reutilizar.
      2. Si no, traemos el user de Zendesk y buscamos por email.
      3. Si no existe localmente, lo creamos sin password usable.
    """
    if zendesk_user_id is None:
        return None
    if zendesk_user_id in cache:
        return cache[zendesk_user_id]

    existing = User.objects.filter(zendesk_id=zendesk_user_id).first()
    if existing:
        cache[zendesk_user_id] = existing
        return existing

    zu = client.user(zendesk_user_id)
    email = (zu.get("email") or "").strip().lower()
    name = zu.get("name") or email or f"zendesk-{zendesk_user_id}"

    if not email:
        # Algunos usuarios en Zendesk no tienen email (canales externos);
        # fabricamos uno estable para no chocar con el unique de User.email.
        email = f"zendesk-{zendesk_user_id}@imported.local"

    user, created = User.objects.get_or_create(
        email=email,
        defaults={"name": name},
    )
    changed = False
    if user.zendesk_id is None:
        user.zendesk_id = zendesk_user_id
        changed = True
    if created:
        user.set_unusable_password()
        try:
            role, _ = Role.objects.get_or_create(role_name="End user")
            user.role = role
        except Exception:
            pass
        changed = True
    if changed:
        user.save()

    cache[zendesk_user_id] = user
    return user


class Command(BaseCommand):
    help = "Importa un ticket de Zendesk (con sus comentarios y requester) en TicketFlow."

    def add_arguments(self, parser):
        parser.add_argument("ticket_id", type=int, help="ID del ticket en Zendesk")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="No persiste nada, solo muestra qué se importaría.",
        )

    def handle(self, *args, **options):
        ticket_id = options["ticket_id"]
        dry_run = options["dry_run"]

        client = ZendeskClient(
            subdomain=getattr(settings, "ZENDESK_SUBDOMAIN", None),
            email=getattr(settings, "ZENDESK_EMAIL", None),
            token=getattr(settings, "ZENDESK_API_TOKEN", None),
        )

        self.stdout.write(f"-> Bajando ticket #{ticket_id} de Zendesk...")
        zt = client.ticket(ticket_id)
        zcomments = client.comments(ticket_id)
        self.stdout.write(
            f"   ticket \"{zt.get('subject')}\" con {len(zcomments)} comentarios"
        )

        mapped_status = STATUS_MAP.get(zt.get("status"), "open")
        mapped_priority = PRIORITY_MAP.get(zt.get("priority") or "normal", "normal")
        attach_total = sum(len(c.get("attachments") or []) for c in zcomments)

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN: no se guarda nada."))
            self.stdout.write(f"   status:       {zt.get('status')} -> {mapped_status}")
            self.stdout.write(f"   priority:     {zt.get('priority') or 'normal'} -> {mapped_priority}")
            self.stdout.write(f"   requester_id: {zt.get('requester_id')}")
            self.stdout.write(f"   assignee_id:  {zt.get('assignee_id')}")
            self.stdout.write(f"   tags:         {zt.get('tags')}")
            if attach_total:
                self.stdout.write(self.style.WARNING(
                    f"   attachments detectados (NO se importan en esta versión): {attach_total}"
                ))
            return

        user_cache = {}
        with transaction.atomic():
            requester = _get_or_create_user(client, zt.get("requester_id"), user_cache)
            if not requester:
                raise CommandError("El ticket de Zendesk no tiene requester resoluble.")
            assignee = _get_or_create_user(client, zt.get("assignee_id"), user_cache)
            submitter = _get_or_create_user(client, zt.get("submitter_id"), user_cache) or requester

            subject = (zt.get("subject") or "(sin asunto)")[:255]
            description = zt.get("description") or ""

            defaults = {
                "subject": subject,
                "description": description,
                "status": mapped_status,
                "priority": mapped_priority,
                "requester": requester,
                "assignee": assignee,
                "created_by": submitter,
                "brand": (str(zt.get("brand_id")) if zt.get("brand_id") else None),
                "type": zt.get("type"),
                "channel": (zt.get("via") or {}).get("channel"),
            }

            ticket, created = Ticket.objects.update_or_create(
                zendesk_id=ticket_id,
                defaults=defaults,
            )

            # Preservamos las fechas originales (auto_now/auto_now_add las pisan en create)
            ts_updates = {}
            created_at = parse_datetime(zt["created_at"]) if zt.get("created_at") else None
            updated_at = parse_datetime(zt["updated_at"]) if zt.get("updated_at") else None
            if created_at:
                ts_updates["created_at"] = created_at
            if updated_at:
                ts_updates["updated_at"] = updated_at
            if mapped_status in ("closed", "resolved") and updated_at:
                ts_updates["closed_at"] = updated_at
            if ts_updates:
                Ticket.objects.filter(pk=ticket.pk).update(**ts_updates)

            # Tags
            tag_objs = []
            for name in zt.get("tags") or []:
                tag, _ = TicketTag.objects.get_or_create(name=name[:255])
                tag_objs.append(tag)
            if tag_objs:
                ticket.tags.set(tag_objs)

            # Comments (públicos + privados/internos)
            new_comments = 0
            for zc in zcomments:
                zc_id = zc.get("id")
                if zc_id and Comment.objects.filter(zendesk_id=zc_id).exists():
                    continue
                author = _get_or_create_user(client, zc.get("author_id"), user_cache) or requester
                comment = Comment.objects.create(
                    ticket=ticket,
                    user=author,
                    content=zc.get("body") or "",
                    is_public=bool(zc.get("public")),
                    zendesk_id=zc_id,
                )
                c_created = parse_datetime(zc["created_at"]) if zc.get("created_at") else None
                if c_created:
                    Comment.objects.filter(pk=comment.pk).update(created_at=c_created)
                new_comments += 1

        verb = "creado" if created else "actualizado"
        self.stdout.write(self.style.SUCCESS(
            f"OK: ticket {verb} (TicketFlow id={ticket.pk}, zendesk_id={ticket_id}) "
            f"con {new_comments} comentarios nuevos."
        ))
        if attach_total:
            self.stdout.write(self.style.WARNING(
                f"   {attach_total} attachments detectados - NO importados (pendiente para la siguiente iteración)."
            ))
