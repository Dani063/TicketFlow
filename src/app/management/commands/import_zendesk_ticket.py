"""
Importa un ticket de Zendesk en TicketFlow.

Uso:
    python manage.py import_zendesk_ticket <zendesk_ticket_id>
    python manage.py import_zendesk_ticket <zendesk_ticket_id> --dry-run
"""

import base64
import time

import requests
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.dateparse import parse_datetime

from app.models import Brand, Comment, Group, Organization, Role, Ticket, TicketEvent, TicketTag, User, ZendeskFieldMap


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

# Zendesk role -> (TicketFlow role name, TicketFlow group name)
ROLE_MAP = {
    "end-user": ("End user", "End users"),
    "agent":    ("agent",    "Agents"),
    "admin":    ("agent",    "Agents"),
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
        for attempt in range(5):
            resp = requests.get(url, headers=self.headers, params=params, timeout=30)
            if resp.status_code == 429:
                wait = int(resp.headers.get("Retry-After", 60))
                time.sleep(wait)
                continue
            break
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

    def group(self, group_id):
        return self.get(f"/groups/{group_id}.json")["group"]

    def brand(self, brand_id):
        return self.get(f"/brands/{brand_id}.json")["brand"]

    def organization(self, org_id):
        return self.get(f"/organizations/{org_id}.json")["organization"]

    def audits(self, ticket_id):
        items = []
        page = 1
        while True:
            data = self.get(f"/tickets/{ticket_id}/audits.json", params={"page": page, "per_page": 100})
            items.extend(data.get("audits", []))
            if not data.get("next_page"):
                break
            page += 1
        return items


# Campos de audits que queremos registrar como TicketEvent
TRACKED_FIELDS = {"status", "assignee_id", "group_id", "priority", "tags", "subject", "requester_id"}

# Labels legibles para field_name
FIELD_LABELS = {
    "status":       "estado",
    "assignee_id":  "asignado",
    "group_id":     "grupo",
    "priority":     "prioridad",
    "tags":         "tags",
    "subject":      "asunto",
    "requester_id": "solicitante",
}


def _resolve_audit_value(field_name, raw_value, user_cache, group_cache, client):
    """Convierte un valor raw de audit en texto legible."""
    if raw_value is None:
        return None
    if field_name == "assignee_id":
        try:
            u = _get_or_create_user(client, int(raw_value), user_cache)
            return u.name if u else str(raw_value)
        except Exception:
            return str(raw_value)
    if field_name == "requester_id":
        try:
            u = _get_or_create_user(client, int(raw_value), user_cache)
            return u.name if u else str(raw_value)
        except Exception:
            return str(raw_value)
    if field_name == "group_id":
        try:
            g = _get_or_create_group(client, int(raw_value), group_cache)
            return g.group_name if g else str(raw_value)
        except Exception:
            return str(raw_value)
    if isinstance(raw_value, list):
        return ", ".join(str(v) for v in raw_value) if raw_value else "—"
    return str(raw_value) if raw_value else None


def _import_audits(client, ticket, zaudits, user_cache, group_cache):
    """Importa los audits de Zendesk como TicketEvent locales."""
    new_events = 0
    for audit in zaudits:
        author_id = audit.get("author_id")
        actor = _get_or_create_user(client, author_id, user_cache) if author_id else None
        created_at = parse_datetime(audit["created_at"]) if audit.get("created_at") else None

        for ev in audit.get("events", []):
            ev_id = ev.get("id")
            ev_type = ev.get("type")

            # Solo Change con campo tracked; ignorar Comments y otros
            if ev_type not in ("Change", "Create"):
                continue
            field = ev.get("field_name")
            if field not in TRACKED_FIELDS:
                continue

            if ev_id and TicketEvent.objects.filter(zendesk_event_id=ev_id).exists():
                continue

            old_raw = ev.get("previous_value")
            new_raw = ev.get("value")

            old_label = _resolve_audit_value(field, old_raw, user_cache, group_cache, client)
            new_label = _resolve_audit_value(field, new_raw, user_cache, group_cache, client)

            TicketEvent.objects.create(
                ticket=ticket,
                actor=actor,
                field_name=FIELD_LABELS.get(field, field),
                old_value=old_label,
                new_value=new_label,
                created_at=created_at,
                zendesk_event_id=ev_id,
            )
            new_events += 1
    return new_events


def _get_or_create_organization(client, org_id, cache):
    if not org_id:
        return None
    if org_id in cache:
        return cache[org_id]
    try:
        zo = client.organization(org_id)
        org, _ = Organization.objects.update_or_create(
            zendesk_id=org_id,
            defaults={
                "name": (zo.get("name") or f"org-{org_id}")[:255],
                "domain_names": ", ".join(zo.get("domain_names") or []),
            },
        )
    except Exception:
        org, _ = Organization.objects.get_or_create(
            zendesk_id=org_id,
            defaults={"name": f"org-{org_id}"},
        )
    cache[org_id] = org
    return org


def _sync_user_profile(client, user, zu, org_cache):
    """Actualiza los campos de perfil del User con los datos de Zendesk."""
    photo = (zu.get("photo") or {})
    photo_url = photo.get("content_url") if photo else None
    org = _get_or_create_organization(client, zu.get("organization_id"), org_cache)

    fields = {
        "phone":        (zu.get("phone") or "")[:64] or None,
        "time_zone":    (zu.get("time_zone") or "")[:100] or None,
        "locale":       (zu.get("locale") or "")[:20] or None,
        "notes":        zu.get("notes") or None,
        "photo_url":    (photo_url or "")[:500] or None,
        "organization": org,
    }
    changed = False
    for attr, val in fields.items():
        if getattr(user, attr) != val:
            setattr(user, attr, val)
            changed = True
    if changed:
        user.save()
    return changed


def _resolve_role_and_group(zendesk_role):
    """Devuelve (Role, Group) locales según el role de Zendesk."""
    role_name, group_name = ROLE_MAP.get(zendesk_role or "end-user", ROLE_MAP["end-user"])
    role, _ = Role.objects.get_or_create(role_name=role_name)
    group, _ = Group.objects.get_or_create(group_name=group_name)
    return role, group


def _get_or_create_user(client, zendesk_user_id, cache, org_cache=None):
    """Crea/recupera el User local equivalente al usuario de Zendesk.

    Estrategia:
      1. Si ya tenemos un User con ese zendesk_id -> reutilizar y refrescar perfil.
      2. Si no, traemos el user de Zendesk y buscamos por email.
      3. Si no existe localmente, lo creamos sin password usable.
    """
    if zendesk_user_id is None:
        return None
    if zendesk_user_id in cache:
        return cache[zendesk_user_id]

    zu = client.user(zendesk_user_id)
    role, group = _resolve_role_and_group(zu.get("role"))

    existing = User.objects.filter(zendesk_id=zendesk_user_id).first()
    if existing:
        changed = False
        if existing.role_id != role.pk:
            existing.role = role; changed = True
        if existing.group_id != group.pk:
            existing.group = group; changed = True
        if changed:
            existing.save()
        if org_cache is not None:
            _sync_user_profile(client, existing, zu, org_cache)
        cache[zendesk_user_id] = existing
        return existing

    email = (zu.get("email") or "").strip().lower()
    name = zu.get("name") or email or f"zendesk-{zendesk_user_id}"
    if not email:
        email = f"zendesk-{zendesk_user_id}@imported.local"

    user, created = User.objects.get_or_create(
        email=email,
        defaults={"name": name, "role": role, "group": group},
    )
    changed = False
    if user.zendesk_id is None:
        user.zendesk_id = zendesk_user_id; changed = True
    if created:
        user.set_unusable_password(); changed = True
    else:
        if user.role_id is None:
            user.role = role; changed = True
        if user.group_id is None:
            user.group = group; changed = True
    if changed:
        user.save()

    if org_cache is not None:
        _sync_user_profile(client, user, zu, org_cache)

    cache[zendesk_user_id] = user
    return user


def _get_or_create_group(client, zendesk_group_id, cache):
    if zendesk_group_id is None:
        return None
    if zendesk_group_id in cache:
        return cache[zendesk_group_id]
    zg = client.group(zendesk_group_id)
    name = (zg.get("name") or f"zendesk-group-{zendesk_group_id}")[:255]
    group, _ = Group.objects.update_or_create(
        zendesk_id=zendesk_group_id,
        defaults={"group_name": name},
    )
    cache[zendesk_group_id] = group
    return group


def _apply_custom_fields(ticket, zt):
    """Aplica los `custom_fields` de Zendesk a los atributos mapeados del Ticket.

    Devuelve True si guardó cambios.
    """
    cfs = zt.get("custom_fields") or []
    if not cfs:
        return False
    mapping = {m.zendesk_field_id: m for m in ZendeskFieldMap.objects.exclude(ticketflow_attr__isnull=True)}
    if not mapping:
        return False
    changed = False
    for cf in cfs:
        fmap = mapping.get(cf.get("id"))
        if not fmap or not fmap.ticketflow_attr:
            continue
        attr = fmap.ticketflow_attr
        if not hasattr(ticket, attr):
            continue
        value = cf.get("value")
        # Normalizar tipos según el tipo de campo de Zendesk
        if fmap.zendesk_type == "checkbox":
            value = bool(value) if value is not None else None
        elif value == "":
            value = None
        if getattr(ticket, attr) != value:
            setattr(ticket, attr, value)
            changed = True
    if changed:
        ticket.save()
    return changed


def _get_or_create_brand(client, zendesk_brand_id, cache):
    if zendesk_brand_id is None:
        return None
    if zendesk_brand_id in cache:
        return cache[zendesk_brand_id]
    try:
        zb = client.brand(zendesk_brand_id)
        brand, _ = Brand.objects.update_or_create(
            zendesk_id=zendesk_brand_id,
            defaults={"name": (zb.get("name") or f"brand-{zendesk_brand_id}")[:255]},
        )
    except Exception:
        brand, _ = Brand.objects.get_or_create(
            zendesk_id=zendesk_brand_id,
            defaults={"name": f"brand-{zendesk_brand_id}"},
        )
    cache[zendesk_brand_id] = brand
    return brand


class Command(BaseCommand):
    help = "Importa un ticket de Zendesk (con sus comentarios y requester) en TicketFlow."

    def add_arguments(self, parser):
        parser.add_argument("ticket_id", type=int, help="ID del ticket en Zendesk")
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="No persiste nada, solo muestra qué se importaría.",
        )
        parser.add_argument(
            "--refresh-comments",
            action="store_true",
            help="Actualiza html_body de comentarios ya importados (útil tras añadir el campo).",
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
        z_priority = zt.get("priority")
        mapped_priority = PRIORITY_MAP.get(z_priority) if z_priority else None
        attach_total = sum(len(c.get("attachments") or []) for c in zcomments)
        cc_ids = list(zt.get("collaborator_ids") or [])

        if dry_run:
            self.stdout.write(self.style.WARNING("DRY-RUN: no se guarda nada."))
            self.stdout.write(f"   status:       {zt.get('status')} -> {mapped_status}")
            self.stdout.write(f"   priority:     {zt.get('priority') or '(none)'} -> {mapped_priority or '(none)'}")
            self.stdout.write(f"   requester_id: {zt.get('requester_id')}")
            self.stdout.write(f"   assignee_id:  {zt.get('assignee_id')}")
            self.stdout.write(f"   group_id:     {zt.get('group_id')}")
            self.stdout.write(f"   brand_id:     {zt.get('brand_id')}")
            self.stdout.write(f"   ccs:          {cc_ids or '(none)'}")
            self.stdout.write(f"   tags:         {zt.get('tags')}")
            if attach_total:
                self.stdout.write(self.style.WARNING(
                    f"   attachments detectados (NO se importan en esta versión): {attach_total}"
                ))
            return

        user_cache = {}
        group_cache = {}
        brand_cache = {}
        org_cache = {}
        with transaction.atomic():
            requester = _get_or_create_user(client, zt.get("requester_id"), user_cache, org_cache)
            if not requester:
                raise CommandError("El ticket de Zendesk no tiene requester resoluble.")
            assignee = _get_or_create_user(client, zt.get("assignee_id"), user_cache, org_cache)
            submitter = _get_or_create_user(client, zt.get("submitter_id"), user_cache, org_cache) or requester
            assigned_group = _get_or_create_group(client, zt.get("group_id"), group_cache)
            brand = _get_or_create_brand(client, zt.get("brand_id"), brand_cache)

            cc_users = []
            for cid in cc_ids:
                u = _get_or_create_user(client, cid, user_cache, org_cache)
                if u:
                    cc_users.append(u)

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
                "brand": brand,
                "assigned_group": assigned_group,
                "type": zt.get("type"),
                "channel": (zt.get("via") or {}).get("channel"),
            }

            ticket, created = Ticket.objects.update_or_create(
                zendesk_id=ticket_id,
                defaults=defaults,
            )

            if cc_users:
                ticket.ccs.set(cc_users)

            _apply_custom_fields(ticket, zt)

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
            due_at = parse_datetime(zt["due_at"]) if zt.get("due_at") else None
            if due_at:
                ts_updates["due_at"] = due_at
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
            refresh = options.get("refresh_comments", False)
            new_comments = 0
            refreshed_comments = 0
            for zc in zcomments:
                zc_id = zc.get("id")
                existing_comment = Comment.objects.filter(zendesk_id=zc_id).first() if zc_id else None
                if existing_comment:
                    if refresh and not existing_comment.html_body:
                        existing_comment.html_body = zc.get("html_body") or ""
                        existing_comment.save()
                        refreshed_comments += 1
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

            # Audits (cambios de estado, reasignaciones, etc.)
            zaudits = client.audits(ticket_id)
            new_events = _import_audits(client, ticket, zaudits, user_cache, group_cache)

        verb = "creado" if created else "actualizado"
        msg = (f"OK: ticket {verb} (TicketFlow id={ticket.pk}, zendesk_id={ticket_id}) "
               f"con {new_comments} comentarios nuevos, {new_events} eventos.")
        if refreshed_comments:
            msg += f" {refreshed_comments} html_body actualizados."
        self.stdout.write(self.style.SUCCESS(msg))
        if attach_total:
            self.stdout.write(self.style.WARNING(
                f"   {attach_total} attachments detectados - NO importados (pendiente para la siguiente iteración)."
            ))
