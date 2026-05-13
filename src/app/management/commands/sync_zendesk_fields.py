"""
Sincroniza las definiciones de custom fields de Zendesk en `ZendeskFieldMap`.

Uso:
    python src/main.py sync_zendesk_fields

Esto descubre todos los campos personalizados de tickets en tu cuenta de Zendesk
y los guarda con su ID + título + tipo. El campo `ticketflow_attr` se rellena
automáticamente para los títulos conocidos (Security related, Monitoring, etc.)
y queda en blanco para el resto (puedes editarlo desde el admin si quieres
mapear más).

Este mapping es lo que permite tanto la importación (id -> attr) como la
exportación futura (attr -> id en el payload de PUT /tickets/{id}).
"""

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from app.management.commands.import_zendesk_ticket import ZendeskClient
from app.models import ZendeskFieldMap


# Mapeo por título de Zendesk -> atributo del modelo Ticket
# (insensible a may/min y a espacios al inicio/fin)
DEFAULT_TITLE_TO_ATTR = {
    # Nota: el custom field "Channel" NO se mapea a Ticket.channel porque
    # ese campo se rellena desde `via.channel` (medio de entrada del ticket).
    "service":                "service",
    "category":               "category",
    "idioma notificaciones":  "language",
    "security related":       "security_related",
    "monitoring":             "monitoring",
    "approval status":        "approval_status",
    "resolution type":        "resolution_type",
    "required tasks":         "required_tasks",
}


class Command(BaseCommand):
    help = "Descubre custom fields de Zendesk y los registra en ZendeskFieldMap."

    def handle(self, *args, **options):
        client = ZendeskClient(
            subdomain=getattr(settings, "ZENDESK_SUBDOMAIN", None),
            email=getattr(settings, "ZENDESK_EMAIL", None),
            token=getattr(settings, "ZENDESK_API_TOKEN", None),
        )
        data = client.get("/ticket_fields.json")
        fields = data.get("ticket_fields") or []

        # Campos de sistema que no consideramos "custom"
        SYSTEM_TYPES = {"subject", "description", "status", "priority",
                        "tickettype", "group", "assignee"}

        created = updated = skipped = 0
        for f in fields:
            if f.get("type") in SYSTEM_TYPES:
                skipped += 1
                continue
            fid = f.get("id")
            title = (f.get("title") or "").strip()
            ftype = f.get("type") or ""
            active = bool(f.get("active"))
            default_attr = DEFAULT_TITLE_TO_ATTR.get(title.lower())

            obj, was_created = ZendeskFieldMap.objects.get_or_create(
                zendesk_field_id=fid,
                defaults={
                    "zendesk_title": title,
                    "zendesk_type": ftype,
                    "active": active,
                    "ticketflow_attr": default_attr,
                },
            )
            if was_created:
                created += 1
                self.stdout.write(self.style.SUCCESS(
                    f"  + {title!r} (id={fid}, type={ftype}) -> {default_attr or '(unmapped)'}"
                ))
                continue

            # Actualizar campos que pueden cambiar en Zendesk
            changed = False
            if obj.zendesk_title != title:
                obj.zendesk_title = title; changed = True
            if obj.zendesk_type != ftype:
                obj.zendesk_type = ftype; changed = True
            if obj.active != active:
                obj.active = active; changed = True
            # Solo poner attr automático si seguimos sin asignación manual
            if not obj.ticketflow_attr and default_attr:
                obj.ticketflow_attr = default_attr; changed = True
            if changed:
                obj.save()
                updated += 1
                self.stdout.write(f"  ~ {title!r} (id={fid}) actualizado")

        self.stdout.write(self.style.SUCCESS(
            f"\nListo. Creados: {created}, actualizados: {updated}, skipped (sistema): {skipped}."
        ))
        unmapped = ZendeskFieldMap.objects.filter(ticketflow_attr__isnull=True, active=True).count()
        if unmapped:
            self.stdout.write(self.style.WARNING(
                f"  Hay {unmapped} campos activos sin mapear. Edítalos en el admin si quieres importarlos."
            ))
