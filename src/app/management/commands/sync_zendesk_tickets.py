"""
Importa/sincroniza tickets de Zendesk en lote.

Estrategias de recolección de IDs:
  --all / --since-date   → Search API mes a mes (robusto, sin timeouts)
  --since N              → Incremental API (últimos N días, rápido para syncs frecuentes)

Optimizaciones de procesamiento:
  - Cachés pre-cargadas desde BD (evita N DB lookups por entidades ya importadas)
  - bulk_create para comentarios y eventos (O(1) queries por ticket)
  - ThreadPoolExecutor para procesar tickets en paralelo (IO-bound: APIs Zendesk)

Uso:
    python main.py sync_zendesk_tickets                          # últimos 7 días
    python main.py sync_zendesk_tickets --since 30               # últimos 30 días
    python main.py sync_zendesk_tickets --all                    # desde 2015 hasta hoy
    python main.py sync_zendesk_tickets --since-date 2022-06-01  # desde una fecha concreta
    python main.py sync_zendesk_tickets --all --workers 10       # más paralelismo
    python main.py sync_zendesk_tickets --dry-run                # solo lista IDs, sin importar
"""

import datetime
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

from django.core.management.base import BaseCommand, CommandError
from django.conf import settings
from django.db import transaction, connection
from django.utils.dateparse import parse_datetime

from app.management.commands.import_zendesk_ticket import (
    ZendeskClient,
    _get_or_create_user,
    _get_or_create_group,
    _apply_custom_fields,
    _import_audits,
    _get_or_create_brand,
    _populate_caches_from_db,
    STATUS_MAP,
    PRIORITY_MAP,
)
from app.models import Comment, Ticket, TicketTag


# ---------------------------------------------------------------------------
# ID collection helpers
# ---------------------------------------------------------------------------

def _next_month(d: datetime.datetime) -> datetime.datetime:
    if d.month == 12:
        return datetime.datetime(d.year + 1, 1, 1)
    return datetime.datetime(d.year, d.month + 1, 1)


def _fetch_period(client, start: datetime.datetime, end: datetime.datetime) -> tuple:
    """
    Fetches ticket IDs for a date range [start, end) via Search API.
    Returns (ids: set, truncated: bool).
    truncated=True when Zendesk's 1000-result cap was hit.
    """
    start_str = start.strftime("%Y-%m-%d")
    end_str   = end.strftime("%Y-%m-%d")
    query = f"type:ticket updated>={start_str} updated<{end_str}"

    ids = set()
    for page in range(1, 11):  # Zendesk hard cap: 10 pages × 100 = 1000 max
        try:
            data = client.get(
                "/search.json",
                params={"query": query, "per_page": 100, "page": page},
                timeout=60,
            )
        except Exception:
            # 422 on page 11+ means >1000 results — signal truncation
            return ids, True

        for t in data.get("results", []):
            tid = t.get("id")
            if tid and t.get("status") != "deleted":
                ids.add(tid)

        if not data.get("next_page"):
            return ids, False  # got everything cleanly

    # Reached page 10 and there's still a next_page → truncated
    return ids, data.get("next_page") is not None


def _collect_period(client, start: datetime.datetime, end: datetime.datetime,
                    seen: set, stdout, lock, depth: int = 0) -> int:
    """
    Recursively collects ticket IDs for [start, end).
    If a period exceeds 1000 results it splits in half and recurses (max depth 5 ≈ ~1 day).
    Returns count of NEW ids added to `seen`.
    """
    ids, truncated = _fetch_period(client, start, end)

    if truncated and depth < 5:
        # Split the period in half and retry each half
        mid = start + (end - start) / 2
        mid = datetime.datetime(mid.year, mid.month, mid.day)
        if mid <= start:
            mid = start + datetime.timedelta(days=1)
        if mid >= end:
            # Period is a single day and still truncated — take what we have
            truncated = False
        else:
            n1 = _collect_period(client, start, mid,  seen, stdout, lock, depth + 1)
            n2 = _collect_period(client, mid,   end,  seen, stdout, lock, depth + 1)
            return n1 + n2

    added = 0
    for tid in ids:
        if tid not in seen:
            seen.add(tid)
            added += 1

    indent = "  " + "  " * depth
    label  = start.strftime("%Y-%m-%d")
    if depth > 0:
        label += f"–{end.strftime('%d')}"
    if added:
        with lock:
            stdout.write(f"{indent}{label}: {added:>4} tickets")

    return added


def _collect_ids_by_months(client, since: datetime.datetime, stdout, lock) -> list:
    """
    Iterates month by month from `since` to now.
    Months with >1000 tickets are automatically split into halves recursively.
    Returns a deduplicated list of ticket IDs.
    """
    now     = datetime.datetime.utcnow()
    seen    = set()
    current = datetime.datetime(since.year, since.month, 1)

    while current <= now:
        next_m = _next_month(current)
        added  = _collect_period(client, current, min(next_m, now), seen, stdout, lock, depth=0)
        if added == 0:
            pass  # silent for empty months
        else:
            with lock:
                stdout.write(f"  {current.strftime('%Y-%m')}: {added:>4} total")
        current = next_m

    return list(seen)


def _collect_ids_incremental(client, start_time: int, stdout) -> list:
    """Uses the Zendesk incremental cursor API. Fast for small windows (last N days)."""
    ticket_ids = []
    url_path = "/incremental/tickets/cursor.json"
    params   = {"start_time": start_time}

    while True:
        data = client.get(url_path, params=params, timeout=120)
        for t in data.get("tickets", []):
            if t.get("id") and t.get("status") != "deleted":
                ticket_ids.append(t["id"])
        after_cursor = data.get("after_cursor")
        if not after_cursor or data.get("end_of_stream"):
            break
        params = {"cursor": after_cursor}

    return ticket_ids


# ---------------------------------------------------------------------------
# Ticket import (single)
# ---------------------------------------------------------------------------

def _import_one(client, ticket_id, caches):
    """
    Importa un ticket de Zendesk. Devuelve ('created'|'updated'|'skipped', pk, subject).
    bulk_create para comentarios: 2 queries por ticket en lugar de N×3.
    """
    user_cache  = caches["users"]
    group_cache = caches["groups"]
    brand_cache = caches["brands"]
    org_cache   = caches["orgs"]

    try:
        zt = client.ticket(ticket_id)
    except CommandError as e:
        return "skipped", None, str(e)

    zcomments = client.comments(ticket_id)

    with transaction.atomic():
        requester = _get_or_create_user(client, zt.get("requester_id"), user_cache, org_cache)
        if not requester:
            return "skipped", None, "sin requester"

        assignee       = _get_or_create_user(client, zt.get("assignee_id"), user_cache, org_cache)
        submitter      = _get_or_create_user(client, zt.get("submitter_id"), user_cache, org_cache) or requester
        assigned_group = _get_or_create_group(client, zt.get("group_id"), group_cache)
        brand          = _get_or_create_brand(client, zt.get("brand_id"), brand_cache)

        cc_users = [
            u for cid in (zt.get("collaborator_ids") or [])
            if (u := _get_or_create_user(client, cid, user_cache, org_cache))
        ]

        defaults = {
            "subject":        (zt.get("subject") or "(sin asunto)")[:255],
            "description":    zt.get("description") or "",
            "status":         STATUS_MAP.get(zt.get("status"), "open"),
            "priority":       PRIORITY_MAP.get(zt.get("priority")) if zt.get("priority") else None,
            "requester":      requester,
            "assignee":       assignee,
            "created_by":     submitter,
            "brand":          brand,
            "assigned_group": assigned_group,
            "type":           zt.get("type"),
            "channel":        (zt.get("via") or {}).get("channel"),
        }

        ticket, created = Ticket.objects.update_or_create(zendesk_id=ticket_id, defaults=defaults)

        if cc_users:
            ticket.ccs.set(cc_users)

        _apply_custom_fields(ticket, zt)

        ts_updates = {}
        for field, raw in [("created_at", zt.get("created_at")), ("updated_at", zt.get("updated_at"))]:
            if raw:
                ts_updates[field] = parse_datetime(raw)
        if defaults["status"] in ("closed", "resolved") and ts_updates.get("updated_at"):
            ts_updates["closed_at"] = ts_updates["updated_at"]
        if zt.get("due_at"):
            ts_updates["due_at"] = parse_datetime(zt["due_at"])
        if ts_updates:
            Ticket.objects.filter(pk=ticket.pk).update(**ts_updates)

        tag_objs = [TicketTag.objects.get_or_create(name=n[:255])[0] for n in (zt.get("tags") or [])]
        if tag_objs:
            ticket.tags.set(tag_objs)

        # Comments: bulk insert — 2 queries regardless of comment count
        zc_ids = [zc.get("id") for zc in zcomments if zc.get("id")]
        existing_zids = set(
            Comment.objects.filter(zendesk_id__in=zc_ids).values_list("zendesk_id", flat=True)
        ) if zc_ids else set()

        new_objs = []
        for zc in zcomments:
            zc_id = zc.get("id")
            if zc_id in existing_zids:
                continue
            author = _get_or_create_user(client, zc.get("author_id"), user_cache, org_cache) or requester
            obj = Comment(
                ticket=ticket,
                user=author,
                content=zc.get("body") or "",
                html_body=zc.get("html_body") or "",
                is_public=bool(zc.get("public")),
                zendesk_id=zc_id,
                via_channel=(zc.get("via") or {}).get("channel") or "",
            )
            c_dt = parse_datetime(zc["created_at"]) if zc.get("created_at") else None
            if c_dt:
                obj.created_at = c_dt
            new_objs.append(obj)

        if new_objs:
            Comment.objects.bulk_create(new_objs, ignore_conflicts=True)

        try:
            zaudits = client.audits(ticket_id)
            _import_audits(client, ticket, zaudits, user_cache, group_cache)
        except Exception:
            pass

    return ("created" if created else "updated"), ticket.pk, defaults["subject"]


# ---------------------------------------------------------------------------
# Management command
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Sincroniza tickets de Zendesk (con paralelismo y bulk inserts)"

    def add_arguments(self, parser):
        parser.add_argument(
            "--since",
            type=int,
            default=7,
            metavar="DAYS",
            help="Importar tickets actualizados en los últimos N días — usa incremental API (default: 7)",
        )
        parser.add_argument(
            "--all",
            action="store_true",
            help="Histórico completo desde 2015 hasta hoy — usa Search API mes a mes",
        )
        parser.add_argument(
            "--since-date",
            metavar="YYYY-MM-DD",
            help="Desde una fecha concreta hasta hoy — usa Search API mes a mes (ej: 2022-01-01)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Lista los tickets encontrados sin importar nada",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=6,
            metavar="N",
            help="Threads paralelos para procesar tickets (default: 6)",
        )
        parser.add_argument(
            "--only-missing",
            action="store_true",
            help="Importar solo IDs que aún no existen en BD (útil para retomar tras fallos)",
        )

    def handle(self, *args, **options):
        sync_all       = options["all"]
        since_days     = options["since"]
        since_date_str = options.get("since_date")
        dry_run        = options["dry_run"]
        only_missing   = options["only_missing"]
        workers        = max(1, options["workers"])

        client = ZendeskClient(
            subdomain=getattr(settings, "ZENDESK_SUBDOMAIN", None),
            email=getattr(settings, "ZENDESK_EMAIL", None),
            token=getattr(settings, "ZENDESK_API_TOKEN", None),
        )

        lock = threading.Lock()

        # --- Collect ticket IDs ---
        if since_date_str or sync_all:
            if since_date_str:
                try:
                    since_dt = datetime.datetime.strptime(since_date_str, "%Y-%m-%d")
                except ValueError:
                    raise CommandError(f"Fecha inválida: '{since_date_str}'. Usa YYYY-MM-DD.")
            else:
                since_dt = datetime.datetime(2015, 1, 1)

            self.stdout.write(
                f"Recolectando tickets desde {since_dt.strftime('%Y-%m-%d')} "
                f"hasta hoy (Search API mes a mes)..."
            )
            ticket_ids = _collect_ids_by_months(client, since_dt, self.stdout, lock)

        else:
            cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=since_days)
            self.stdout.write(
                f"Buscando tickets actualizados en los últimos {since_days} días "
                f"({cutoff.strftime('%Y-%m-%d %H:%M UTC')}) — incremental API..."
            )
            ticket_ids = _collect_ids_incremental(client, int(cutoff.timestamp()), self.stdout)

        total = len(ticket_ids)
        self.stdout.write(f"\n  Total únicos: {total} tickets")

        if only_missing and ticket_ids:
            self.stdout.write("Filtrando IDs ya presentes en BD...")
            existing = set(
                Ticket.objects.filter(zendesk_id__isnull=False)
                .values_list("zendesk_id", flat=True)
            )
            ticket_ids = [tid for tid in ticket_ids if tid not in existing]
            self.stdout.write(
                f"  {len(existing)} ya en BD, {total - len(ticket_ids)} omitidos — "
                f"{len(ticket_ids)} pendientes de importar"
            )
            total = len(ticket_ids)

        if dry_run or total == 0:
            if dry_run:
                for tid in ticket_ids:
                    self.stdout.write(f"  [dry] #{tid}")
            self.stdout.write(self.style.SUCCESS(
                f"Dry-run: {total} tickets encontrados, ninguno importado." if dry_run else "Nada que importar."
            ))
            return

        # --- Pre-populate entity caches from DB ---
        self.stdout.write("Pre-cargando entidades locales desde BD...")
        caches = {"users": {}, "groups": {}, "brands": {}, "orgs": {}}
        _populate_caches_from_db(
            caches["users"], caches["groups"], caches["brands"], caches["orgs"]
        )
        self.stdout.write(
            f"  {len(caches['users'])} usuarios, {len(caches['groups'])} grupos, "
            f"{len(caches['brands'])} brands en caché\n"
        )

        # --- Parallel processing ---
        self.stdout.write(f"Procesando {total} tickets con {workers} workers...\n")
        counter  = [0]
        results  = {"created": 0, "updated": 0, "skipped": 0}

        def _worker(ticket_id):
            try:
                result, pk, subject = _import_one(client, ticket_id, caches)
            except Exception as exc:
                result, subject = "skipped", str(exc)
            finally:
                connection.close()

            with lock:
                counter[0] += 1
                icon = "+" if result == "created" else ("~" if result == "updated" else "!")
                self.stdout.write(
                    f"[{counter[0]}/{total}] {icon} #{ticket_id} "
                    f"{subject[:55] if isinstance(subject, str) else ''}"
                )
            return result

        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(_worker, tid): tid for tid in ticket_ids}
            for future in as_completed(futures):
                try:
                    r = future.result()
                    with lock:
                        results[r if r in results else "skipped"] += 1
                except Exception as exc:
                    tid = futures[future]
                    with lock:
                        self.stderr.write(f"  ERROR #{tid}: {exc}")
                        results["skipped"] += 1

        self.stdout.write(self.style.SUCCESS(
            f"\nSync completo: {results['created']} creados, {results['updated']} actualizados"
            + (f", {results['skipped']} omitidos" if results["skipped"] else "") + "."
        ))
