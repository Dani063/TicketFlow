"""
Importa/sincroniza tickets de Zendesk en lote usando la API incremental.

Uso:
    python manage.py sync_zendesk_tickets                   # últimos 7 días
    python manage.py sync_zendesk_tickets --since 30        # últimos 30 días
    python manage.py sync_zendesk_tickets --since 1         # últimas 24 h
    python manage.py sync_zendesk_tickets --dry-run         # solo lista IDs
"""

import datetime

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings

# Reutilizamos toda la lógica del comando de ticket individual
from app.management.commands.import_zendesk_ticket import (
    ZendeskClient,
    _get_or_create_user,
    _get_or_create_group,
    _apply_custom_fields,
    _import_audits,
    _resolve_brand_name,
    STATUS_MAP,
    PRIORITY_MAP,
)
from app.models import Comment, Ticket, TicketTag
from django.utils.dateparse import parse_datetime
from django.db import transaction


def _import_one(client, ticket_id, stdout, dry_run=False):
    """Importa un único ticket de Zendesk. Devuelve ('created'|'updated'|'skipped', ticket_pk)."""
    try:
        zt = client.ticket(ticket_id)
    except CommandError as e:
        stdout.write(f"  ! #{ticket_id}: {e}")
        return 'skipped', None

    if dry_run:
        stdout.write(f"  [dry] #{ticket_id}: {zt.get('subject', '')[:60]}")
        return 'skipped', None

    user_cache   = {}
    group_cache  = {}
    brand_cache  = {}
    org_cache    = {}

    zcomments = client.comments(ticket_id)

    with transaction.atomic():
        requester = _get_or_create_user(client, zt.get("requester_id"), user_cache, org_cache)
        if not requester:
            stdout.write(f"  ! #{ticket_id}: sin requester, omitido")
            return 'skipped', None

        assignee       = _get_or_create_user(client, zt.get("assignee_id"), user_cache, org_cache)
        submitter      = _get_or_create_user(client, zt.get("submitter_id"), user_cache, org_cache) or requester
        assigned_group = _get_or_create_group(client, zt.get("group_id"), group_cache)
        brand_name     = _resolve_brand_name(client, zt.get("brand_id"), brand_cache)

        cc_users = []
        for cid in list(zt.get("collaborator_ids") or []):
            u = _get_or_create_user(client, cid, user_cache, org_cache)
            if u:
                cc_users.append(u)

        mapped_status   = STATUS_MAP.get(zt.get("status"), "open")
        z_priority      = zt.get("priority")
        mapped_priority = PRIORITY_MAP.get(z_priority) if z_priority else None

        defaults = {
            "subject":        (zt.get("subject") or "(sin asunto)")[:255],
            "description":    zt.get("description") or "",
            "status":         mapped_status,
            "priority":       mapped_priority,
            "requester":      requester,
            "assignee":       assignee,
            "created_by":     submitter,
            "brand":          brand_name,
            "assigned_group": assigned_group,
            "type":           zt.get("type"),
            "channel":        (zt.get("via") or {}).get("channel"),
        }

        ticket, created = Ticket.objects.update_or_create(
            zendesk_id=ticket_id,
            defaults=defaults,
        )

        if cc_users:
            ticket.ccs.set(cc_users)

        _apply_custom_fields(ticket, zt)

        ts_updates = {}
        created_at = parse_datetime(zt["created_at"]) if zt.get("created_at") else None
        updated_at = parse_datetime(zt["updated_at"]) if zt.get("updated_at") else None
        if created_at:
            ts_updates["created_at"] = created_at
        if updated_at:
            ts_updates["updated_at"] = updated_at
        if mapped_status in ("closed", "resolved") and updated_at:
            ts_updates["closed_at"] = updated_at
        due_at = parse_datetime(zt["due_at"]) if zt.get("due_at") else None
        if due_at:
            ts_updates["due_at"] = due_at
        if ts_updates:
            Ticket.objects.filter(pk=ticket.pk).update(**ts_updates)

        for name in zt.get("tags") or []:
            tag, _ = TicketTag.objects.get_or_create(name=name[:255])
        tag_objs = [TicketTag.objects.get_or_create(name=n[:255])[0] for n in (zt.get("tags") or [])]
        if tag_objs:
            ticket.tags.set(tag_objs)

        new_comments = 0
        for zc in zcomments:
            zc_id = zc.get("id")
            if zc_id and Comment.objects.filter(zendesk_id=zc_id).exists():
                continue
            author = _get_or_create_user(client, zc.get("author_id"), user_cache, org_cache) or requester
            comment = Comment.objects.create(
                ticket=ticket,
                user=author,
                content=zc.get("body") or "",
                html_body=zc.get("html_body") or "",
                is_public=bool(zc.get("public")),
                zendesk_id=zc_id,
                via_channel=(zc.get("via") or {}).get("channel") or "",
            )
            c_created = parse_datetime(zc["created_at"]) if zc.get("created_at") else None
            if c_created:
                Comment.objects.filter(pk=comment.pk).update(created_at=c_created)
            new_comments += 1

        try:
            zaudits = client.audits(ticket_id)
            _import_audits(client, ticket, zaudits, user_cache, group_cache)
        except Exception:
            pass  # audits no son críticos

    verb = "+" if created else "~"
    stdout.write(f"  {verb} #{ticket_id}: {defaults['subject'][:55]} ({new_comments} comentarios nuevos)")
    return ('created' if created else 'updated'), ticket.pk


class Command(BaseCommand):
    help = "Sincroniza tickets actualizados recientemente desde Zendesk"

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            type=int,
            default=7,
            metavar="DAYS",
            help="Importar tickets actualizados en los últimos N días (default: 7)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Lista los tickets encontrados sin importar nada",
        )

    def handle(self, *args, **options):
        since_days = options["since"]
        dry_run    = options["dry_run"]

        client = ZendeskClient(
            subdomain=getattr(settings, "ZENDESK_SUBDOMAIN", None),
            email=getattr(settings, "ZENDESK_EMAIL", None),
            token=getattr(settings, "ZENDESK_API_TOKEN", None),
        )

        cutoff     = datetime.datetime.utcnow() - datetime.timedelta(days=since_days)
        start_time = int(cutoff.timestamp())

        self.stdout.write(
            f"Buscando tickets actualizados desde hace {since_days} días "
            f"({cutoff.strftime('%Y-%m-%d %H:%M UTC')})..."
        )

        # Usamos la API incremental de Zendesk (cursor-based)
        ticket_ids = []
        url_path   = "/incremental/tickets/cursor.json"
        params     = {"start_time": start_time}

        while True:
            data = client.get(url_path, params=params)
            batch = data.get("tickets", [])
            for t in batch:
                if not t.get("id"):
                    continue
                # La API incremental incluye tickets borrados; los filtramos
                if t.get("status") == "deleted":
                    continue
                ticket_ids.append(t["id"])

            after_cursor = data.get("after_cursor")
            if not after_cursor or data.get("end_of_stream"):
                break
            # Siguiente página via cursor
            url_path = "/incremental/tickets/cursor.json"
            params   = {"cursor": after_cursor}

        total = len(ticket_ids)
        self.stdout.write(f"  {total} tickets encontrados")

        if dry_run or total == 0:
            if dry_run:
                for tid in ticket_ids:
                    self.stdout.write(f"  [dry] #{tid}")
            self.stdout.write(self.style.SUCCESS(
                f"Dry-run completo: {total} tickets encontrados, ninguno importado."
                if dry_run else "Nada que importar."
            ))
            return

        created = updated = skipped = 0
        for i, tid in enumerate(ticket_ids, 1):
            self.stdout.write(f"[{i}/{total}]", ending=" ")
            result, _ = _import_one(client, tid, self.stdout, dry_run=False)
            if result == 'created':
                created += 1
            elif result == 'updated':
                updated += 1
            else:
                skipped += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nSync completo: {created} creados, {updated} actualizados"
            + (f", {skipped} omitidos" if skipped else "") + "."
        ))
