"""Clasificación ITIL de tickets con IA (Azure OpenAI).

Política de aplicación: SOLO se rellenan campos vacíos y con confianza >= umbral
(AI_AUTO_APPLY_CONFIDENCE). Lo que escribió una persona nunca se sobrescribe; la
sugerencia queda en TicketAIAnalysis para mostrarse en la UI. Cada campo aplicado
emite un TicketEvent con el usuario sistema de IA, de modo que el agente lo ve y
puede corregirlo — esa corrección es la señal de feedback del Q3.
"""

import hashlib
import logging

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.constants import TICKET_TYPE_VALUES
from app.models import Role, Ticket, TicketAIAnalysis, TicketEvent, User
from app.services.metrics import record_metric
from app.services import ai

logger = logging.getLogger(__name__)

PROMPT_VERSION = "v1"
LANGUAGE_VALUES = {"es", "en"}
PRIORITY_VALUES = {"low", "normal", "high", "urgent"}

AI_SYSTEM_EMAIL = "ai-assistant@ticketflow.local"

CLASSIFICATION_SCHEMA = {
    "name": "ticket_classification",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["ticket_type", "category", "priority", "language", "confidence", "reasoning"],
        "properties": {
            "ticket_type": {"type": "string", "enum": ["question", "incident", "problem", "task"]},
            "category": {"type": "string"},
            "priority": {"type": "string", "enum": ["low", "normal", "high", "urgent"]},
            "language": {"type": "string", "enum": ["es", "en"]},
            "confidence": {
                "type": "object",
                "additionalProperties": False,
                "required": ["ticket_type", "category", "priority", "language"],
                "properties": {
                    "ticket_type": {"type": "number"},
                    "category": {"type": "number"},
                    "priority": {"type": "number"},
                    "language": {"type": "number"},
                },
            },
            "reasoning": {"type": "string"},
        },
    },
}

SYSTEM_PROMPT = """Eres un clasificador de tickets para una herramienta ITSM (TicketFlow).
Clasifica el ticket según ITIL:
- "incident": interrupción o degradación NO planificada de un servicio que afecta al usuario AHORA \
(caídas, errores, "no puedo acceder", algo que funcionaba dejó de funcionar).
- "problem": causa raíz subyacente de uno o más incidentes; incidentes RECURRENTES o petición de \
análisis de causa raíz ("vuelve a pasar cada semana", "siempre que..."). Regla clave: un fallo \
puntual = incident; un patrón repetido o investigación de causa = problem.
- "question": petición de información o cómo-hacer, sin servicio afectado.
- "task": petición de acción o cambio planificado (alta de usuario, permisos, instalación).

Categoría: elige la MÁS apropiada de la lista proporcionada; si ninguna encaja, propón una breve \
(máximo 4 palabras) en el idioma del ticket.
Prioridad sugerida: "urgent" (servicio crítico caído / muchos usuarios / seguridad), "high" \
(usuario bloqueado sin alternativa), "normal" (impacto limitado), "low" (sin urgencia).
Idioma: "es" o "en" según el texto del solicitante.
Para cada campo da una confianza entre 0.0 y 1.0. En "reasoning", 1-2 frases.

Ejemplos:
- "El portal da error 500 desde las 9:00" -> incident
- "Cada lunes el informe vuelve a llegar vacío, ya van 4 semanas" -> problem
- "¿Cómo exporto mis facturas?" -> question
- "Necesito acceso al SharePoint de finanzas para un compañero nuevo" -> task

Responde SOLO con JSON."""


class TicketAIService:
    @staticmethod
    def get_system_user():
        """Usuario actor de los TicketEvent generados por la IA."""
        user, created = User.objects.get_or_create(
            email=AI_SYSTEM_EMAIL,
            defaults={"name": "AI Assistant"},
        )
        if created:
            user.set_unusable_password()
            try:
                role, _ = Role.objects.get_or_create(role_name="End user")
                user.role = role
            except Exception:
                pass
            user.save()
        return user

    @staticmethod
    def get_category_choices(brand):
        """Categorías candidatas para el prompt: las ya usadas por la marca
        (fallback: globales), cap 50. Aislado aquí a propósito: si la taxonomía
        de categorías evoluciona, solo cambia esta función."""
        qs = Ticket.objects.filter(is_deleted=False).exclude(category__isnull=True).exclude(category="")
        # order_by explícito: sin él, DISTINCT + slice devuelve un subconjunto no
        # determinista (el orden depende del plan del motor) y el prompt varía
        # entre ejecuciones sin motivo.
        if brand is not None:
            brand_categories = list(
                qs.filter(brand=brand).order_by("category").values_list("category", flat=True).distinct()[:50]
            )
            if brand_categories:
                return brand_categories
        return list(qs.order_by("category").values_list("category", flat=True).distinct()[:50])

    @staticmethod
    def build_input(ticket):
        """-> (messages, fingerprint, excerpt)"""
        max_chars = settings.AI_MAX_BODY_CHARS
        body = (ticket.description or "").strip()
        truncated = len(body) > max_chars
        body = body[:max_chars] + ("…[truncado]" if truncated else "")
        brand = ticket.brand if ticket.brand_id else None
        categories = TicketAIService.get_category_choices(brand)
        user_prompt = (
            f"Marca/entidad: {brand.name if brand else '-'} | "
            f"Canal: {ticket.channel or '-'} | Servicio: {ticket.service or '-'}\n"
            f"Categorías disponibles: {', '.join(categories) if categories else '(ninguna definida — propón una)'}\n"
            f"Asunto: {ticket.subject}\n"
            f"Cuerpo:\n{body}"
        )
        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_prompt},
        ]
        fingerprint = hashlib.sha256(
            f"{PROMPT_VERSION}:{ticket.subject}:{body}".encode("utf-8")
        ).hexdigest()
        return messages, fingerprint, user_prompt

    @staticmethod
    def _validate(parsed):
        """Valida enums campo a campo (nunca confiar en el wire). Devuelve un dict
        de sugerencias saneadas; los campos fuera de enum se descartan."""
        confidences = parsed.get("confidence") or {}

        def conf(field):
            try:
                return max(0.0, min(1.0, float(confidences.get(field, 0.0))))
            except (TypeError, ValueError):
                return 0.0

        suggestions = {}
        ticket_type = (parsed.get("ticket_type") or "").strip().lower()
        if ticket_type in TICKET_TYPE_VALUES:
            suggestions["type"] = (ticket_type, conf("ticket_type"))
        category = (parsed.get("category") or "").strip()
        if category:
            suggestions["category"] = (category[:255], conf("category"))
        priority = (parsed.get("priority") or "").strip().lower()
        if priority in PRIORITY_VALUES:
            suggestions["priority"] = (priority, conf("priority"))
        language = (parsed.get("language") or "").strip().lower()
        if language in LANGUAGE_VALUES:
            suggestions["language"] = (language, conf("language"))
        return suggestions

    @staticmethod
    def classify_and_apply(ticket, force=False):
        """Cuerpo de la tarea Celery. Devuelve un string de resultado."""
        messages, fingerprint, excerpt = TicketAIService.build_input(ticket)

        if not force and TicketAIAnalysis.objects.filter(
            ticket=ticket,
            kind=TicketAIAnalysis.KIND_CLASSIFICATION,
            status=TicketAIAnalysis.STATUS_SUCCESS,
            input_fingerprint=fingerprint,
        ).exists():
            return "skip_dup"  # redelivery de SQS o doble dispatch

        # Llamada HTTP FUERA de cualquier lock/transacción (hasta 30s)
        parsed, meta = ai.chat_json(messages, json_schema=CLASSIFICATION_SCHEMA)
        suggestions = TicketAIService._validate(parsed)
        confidences = {field: conf for field, (_, conf) in suggestions.items()}

        analysis = TicketAIAnalysis(
            ticket=ticket,
            kind=TicketAIAnalysis.KIND_CLASSIFICATION,
            status=TicketAIAnalysis.STATUS_SUCCESS,
            suggested_type=suggestions.get("type", (None, 0))[0],
            suggested_category=suggestions.get("category", (None, 0))[0],
            suggested_priority=suggestions.get("priority", (None, 0))[0],
            suggested_language=suggestions.get("language", (None, 0))[0],
            confidence=min(confidences.values()) if confidences else None,
            field_confidences=confidences,
            reasoning=(parsed.get("reasoning") or "")[:2000],
            input_fingerprint=fingerprint,
            input_excerpt=excerpt,
            raw_response=parsed,
            model_name=meta.get("model") or "",
            prompt_version=PROMPT_VERSION,
            input_tokens=meta.get("input_tokens"),
            output_tokens=meta.get("output_tokens"),
            latency_ms=meta.get("latency_ms"),
        )

        applied = TicketAIService._apply(ticket, suggestions, analysis)
        analysis.applied_fields = applied
        analysis.save()
        record_metric(
            "ai.classification.success",
            labels={"ticket_id": ticket.id, "applied": len(applied)},
        )
        return f"applied:{','.join(applied) if applied else 'none'}"

    @staticmethod
    def _apply(ticket, suggestions, analysis):
        """Aplica las sugerencias bajo la política fill-empty-only + confianza.
        Lock corto solo durante la escritura."""
        if not getattr(settings, "AI_AUTO_APPLY", False):
            return []
        threshold = settings.AI_AUTO_APPLY_CONFIDENCE
        actor = TicketAIService.get_system_user()
        applied = []
        now = timezone.now()

        with transaction.atomic():
            locked = (
                Ticket.objects.select_for_update()
                .filter(id=ticket.id, is_deleted=False, merged_into__isnull=True)
                .first()
            )
            if locked is None:
                return []  # borrado/fusionado mientras se clasificaba

            update_fields = []
            for field, (value, conf) in suggestions.items():
                current = getattr(locked, field)
                if current not in (None, ""):
                    continue  # lo puso un humano (o ya estaba): no tocar
                if conf < threshold:
                    continue
                setattr(locked, field, value)
                update_fields.append(field)
                applied.append(field)
                TicketEvent.objects.create(
                    ticket=locked,
                    actor=actor,
                    field_name=field,
                    old_value=None,
                    new_value=value,
                    created_at=now,
                )
            if update_fields:
                locked.save(update_fields=update_fields)

        if "priority" in applied:
            # Con prioridad recién fijada, los plazos de SLA pueden por fin
            # computarse (las políticas suelen filtrar por prioridad).
            # Fuera del lock a propósito: apply_policy solo escribe los due de SLA
            # (derivados de created_at, inmutable, + la prioridad recién persistida
            # que refresh_from_db recarga). Esos campos son disjuntos de la columna
            # priority, así que no hay carrera; mantener el lock corto evita
            # retenerlo durante las queries de find_policy.
            from app.services.sla import SLAService
            ticket.refresh_from_db()
            SLAService.apply_policy(ticket)

        return applied

    @staticmethod
    def mark_failed(ticket, exc):
        try:
            messages, fingerprint, excerpt = TicketAIService.build_input(ticket)
        except Exception:
            fingerprint, excerpt = "", ""
        TicketAIAnalysis.objects.create(
            ticket=ticket,
            kind=TicketAIAnalysis.KIND_CLASSIFICATION,
            status=TicketAIAnalysis.STATUS_FAILED,
            input_fingerprint=fingerprint,
            input_excerpt=excerpt,
            prompt_version=PROMPT_VERSION,
            error=str(exc)[:2000],
        )
