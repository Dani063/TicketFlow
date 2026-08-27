"""
Importa/sincroniza valoraciones de satisfacción de Zendesk en TicketFlow.

Estrategia de paginación (en orden de preferencia):
  1. Cursor-based (page[size] / page[after]) — sin límite de páginas.
  2. Offset clásico con restart manual al llegar a página 99:
     al completar la página 99, reinicia con start_time = created_at del
     último registro, avanzando el cursor temporalmente.

Paralelismo: los 4 scores (offered, unoffered, good, bad) son mutuamente
  excluyentes, así que se fetchan en 4 threads independientes sin solapamiento
  ni deduplicación entre ellos.

Escritura en BD: bulk_create + bulk_update en lugar de N update_or_create.

Uso:
    python main.py sync_zendesk_satisfaction
    python main.py sync_zendesk_satisfaction --since 30
    python main.py sync_zendesk_satisfaction --all
"""

import base64
import datetime
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils.dateparse import parse_datetime

from app.models import SatisfactionRating, Ticket, User


PAGE_SIZE  = 100
BULK_CHUNK = 500

# Valores de score conocidos — mutuamente excluyentes → paralelismo sin solapamiento
SCORES = ["offered", "unoffered", "good", "bad"]

UPDATE_FIELDS = [
    "ticket_id", "score", "comment", "reason",
    "requester_id", "assignee_id", "created_at", "updated_at",
]


# ---------------------------------------------------------------------------
# HTTP helper
# ---------------------------------------------------------------------------

def _make_auth_header(email, token):
    creds = f"{email}/token:{token}".encode()
    return "Basic " + base64.b64encode(creds).decode()


def _get(url, headers, params=None, timeout=30):
    """GET con retry automático ante 429."""
    while True:
        resp = requests.get(url, headers=headers, params=params, timeout=timeout)
        if resp.status_code == 401:
            raise CommandError("Zendesk rechazó las credenciales (401).")
        if resp.status_code == 429:
            time.sleep(int(resp.headers.get("Retry-After", 10)))
            continue
        resp.raise_for_status()
        return resp.json()


# ---------------------------------------------------------------------------
# Fetch por score
# ---------------------------------------------------------------------------

def _fetch_by_score(base_url, auth_header, score, start_ts=None):
    """
    Descarga TODOS los ratings con el score indicado.

    · Prueba cursor-based (page[size]) en la primera petición.
      Si la respuesta incluye meta.after_cursor → lo usa sin límite de páginas.
    · Fallback: offset pagination; cuando llega a página 99 reinicia la serie
      desde el created_at del último registro (avance manual de cursor).
      seen_ids evita duplicados en el borde del restart.
    """
    url     = base_url + "/satisfaction_ratings.json"
    headers = {"Authorization": auth_header}

    base_params = {"score": score, "sort_order": "asc"}
    if start_ts:
        base_params["start_time"] = start_ts

    # Primera petición — prueba cursor-based
    first = _get(url, headers, {**base_params, "page[size]": PAGE_SIZE})
    meta  = first.get("meta") or {}

    # ── Cursor-based (sin límite de páginas) ──────────────────────────────
    if "after_cursor" in meta:
        ratings = list(first.get("satisfaction_ratings", []))
        while meta.get("has_more"):
            cursor = meta.get("after_cursor")
            if not cursor:
                break
            data = _get(url, headers, {"page[size]": PAGE_SIZE, "page[after]": cursor})
            ratings.extend(data.get("satisfaction_ratings", []))
            meta = data.get("meta") or {}
        return ratings

    # ── Fallback: offset con restart manual al llegar a página 99 ─────────
    ratings  = []
    seen_ids = set()

    def _absorb(batch):
        for r in batch:
            if r["id"] not in seen_ids:
                seen_ids.add(r["id"])
                ratings.append(r)

    def _fetch_segment(params_seg):
        """Fetcha páginas 1..99. Retorna (último_registro, hit_limit)."""
        last = None
        for p in range(1, 100):
            data  = _get(url, headers, {**params_seg, "per_page": PAGE_SIZE, "page": p})
            batch = data.get("satisfaction_ratings", [])
            if not batch:
                return last, False
            _absorb(batch)
            last = batch[-1]
            if not data.get("next_page"):
                return last, False
        return last, True  # llegó a pág 99 con next_page

    last_record, hit_limit = _fetch_segment(base_params)

    # Restart loop: avanza current_ts hasta agotar todos los registros
    current_ts = start_ts
    while hit_limit and last_record:
        last_ct = _parse_dt(last_record.get("created_at"))
        if not last_ct:
            break
        new_ts = int(last_ct.timestamp())
        if new_ts == current_ts:
            break  # sin progreso → evitar bucle infinito
        current_ts    = new_ts
        last_record, hit_limit = _fetch_segment({**base_params, "start_time": current_ts})

    return ratings


# ---------------------------------------------------------------------------
# Helpers de conversión y BD
# ---------------------------------------------------------------------------

def _parse_dt(value):
    if not value:
        return None
    try:
        return parse_datetime(value)
    except Exception:
        return None


def _raw_to_obj(zr, ticket_map, user_map):
    ztid = zr.get("ticket_id")
    if not ztid:
        return None
    return SatisfactionRating(
        zendesk_id=zr["id"],
        ticket_id=ticket_map.get(ztid),
        score=zr.get("score", ""),
        comment=(zr.get("comment") or "").strip() or None,
        reason=(zr.get("reason") or "").strip() or None,
        requester_id=user_map.get(zr["requester_id"]) if zr.get("requester_id") else None,
        assignee_id=user_map.get(zr["assignee_id"]) if zr.get("assignee_id") else None,
        created_at=_parse_dt(zr.get("created_at")),
        updated_at=_parse_dt(zr.get("updated_at")),
    )


def _bulk_upsert(objs, existing_ids):
    to_create = [o for o in objs if o.zendesk_id not in existing_ids]
    to_update = [o for o in objs if o.zendesk_id in existing_ids]
    created = updated = 0

    for i in range(0, len(to_create), BULK_CHUNK):
        chunk = to_create[i:i + BULK_CHUNK]
        SatisfactionRating.objects.bulk_create(chunk, ignore_conflicts=True)
        created += len(chunk)

    if to_update:
        pk_map = dict(
            SatisfactionRating.objects.filter(
                zendesk_id__in=[o.zendesk_id for o in to_update]
            ).values_list("zendesk_id", "pk")
        )
        valid = [o for o in to_update if pk_map.get(o.zendesk_id)]
        for obj in valid:
            obj.pk = pk_map[obj.zendesk_id]
        for i in range(0, len(valid), BULK_CHUNK):
            SatisfactionRating.objects.bulk_update(valid[i:i + BULK_CHUNK], UPDATE_FIELDS)
            updated += len(valid[i:i + BULK_CHUNK])

    return created, updated


# ---------------------------------------------------------------------------
# Comando
# ---------------------------------------------------------------------------

class Command(BaseCommand):
    help = "Sincroniza valoraciones de satisfacción desde Zendesk"

    def add_arguments(self, parser):
        parser.add_argument(
            "--since", type=int, default=90, metavar="DAYS",
            help="Importar valoraciones de los últimos N días (default: 90)",
        )
        parser.add_argument(
            "--all", action="store_true",
            help="Importar todas las valoraciones sin límite de fecha",
        )

    def handle(self, *args, **options):
        subdomain = getattr(settings, "ZENDESK_SUBDOMAIN", "")
        email     = getattr(settings, "ZENDESK_EMAIL", "")
        token     = getattr(settings, "ZENDESK_API_TOKEN", "")
        if not (subdomain and email and token):
            raise CommandError(
                "Faltan credenciales de Zendesk. Define ZENDESK_SUBDOMAIN, "
                "ZENDESK_EMAIL y ZENDESK_API_TOKEN en el .env."
            )

        base_url    = f"https://{subdomain}.zendesk.com/api/v2"
        auth_header = _make_auth_header(email, token)

        # --- Precargar mapas ---
        ticket_map = dict(
            Ticket.objects.exclude(zendesk_id__isnull=True).values_list("zendesk_id", "id")
        )
        user_map = dict(
            User.objects.exclude(zendesk_id__isnull=True).values_list("zendesk_id", "id")
        )
        self.stdout.write(
            f"  Mapa local: {len(ticket_map)} tickets, {len(user_map)} usuarios"
        )

        # --- start_time ---
        start_ts = None
        if not options["all"]:
            cutoff   = datetime.datetime.utcnow() - datetime.timedelta(days=options["since"])
            start_ts = int(cutoff.timestamp())

        self.stdout.write(
            f"  Fetching {len(SCORES)} scores en paralelo…"
        )

        # --- Fetch paralelo por score ---
        all_raw = []
        with ThreadPoolExecutor(max_workers=len(SCORES)) as pool:
            futures = {
                pool.submit(_fetch_by_score, base_url, auth_header, score, start_ts): score
                for score in SCORES
            }
            for fut in as_completed(futures):
                score = futures[fut]
                try:
                    score_ratings = fut.result()
                    all_raw.extend(score_ratings)
                    self.stdout.write(
                        f"    {score:>10}: {len(score_ratings):>6} ratings"
                    )
                except Exception as exc:
                    self.stderr.write(f"  ! Error en score '{score}': {exc}")

        self.stdout.write(f"  Total: {len(all_raw)} ratings")

        # --- Convertir ---
        objs, skipped = [], 0
        for zr in all_raw:
            obj = _raw_to_obj(zr, ticket_map, user_map)
            if obj is None:
                skipped += 1
            else:
                objs.append(obj)

        # --- Detectar existentes (1 query) ---
        all_zids = [o.zendesk_id for o in objs]
        existing_ids = set(
            SatisfactionRating.objects.filter(zendesk_id__in=all_zids)
            .values_list("zendesk_id", flat=True)
        ) if all_zids else set()

        # --- Bulk upsert ---
        created, updated = _bulk_upsert(objs, existing_ids)

        self.stdout.write(self.style.SUCCESS(
            f"Sync completo: {created} creadas, {updated} actualizadas"
            + (f", {skipped} sin ticket_id" if skipped else "") + "."
        ))
