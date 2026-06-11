"""Asistencia de respuesta IA: borradores a partir del ticket, sus comentarios
públicos y casos similares ya resueltos.

No persiste nada: devuelve borradores que el agente edita e inserta en el editor.
El idioma de salida es el del solicitante (ticket.language -> brand.language -> es).
"""

import logging

from django.conf import settings

from app.constants import normalize_language
from app.models import Comment
from app.permissions import is_agent
from app.services import ai
from app.services.metrics import record_metric
from app.services.similar import SimilarTicketIndex

logger = logging.getLogger(__name__)

PROMPT_VERSION = "reply-v1"
MAX_COMMENT_CHARS = 1500
MAX_COMMENTS = 8

REPLY_SCHEMA = {
    "name": "ticket_reply_suggestions",
    "strict": True,
    "schema": {
        "type": "object",
        "additionalProperties": False,
        "required": ["language", "drafts"],
        "properties": {
            "language": {"type": "string", "enum": ["es", "en"]},
            "drafts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "additionalProperties": False,
                    "required": ["title", "body"],
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                    },
                },
            },
        },
    },
}

SYSTEM_PROMPT = """Eres un agente de soporte experto de la marca {brand}. Redactas \
respuestas para el CLIENTE de un ticket de soporte.
Reglas:
- Escribe SIEMPRE en {lang_name} ({lang}).
- Tono profesional, cercano y resolutivo. Trata de usted.
- Apóyate en los CASOS SIMILARES ya resueltos que se te dan: reutiliza lo que funcionó, \
pero NO inventes datos, identificadores, plazos ni promesas que no aparezcan en el contexto.
- Si falta información para resolver, pide en el borrador los datos concretos que necesitas.
- No incluyas la línea de saludo del sistema ni firmes con un nombre concreto (deja "[Tu nombre]" \
o el cierre estándar del agente).
- Devuelve 2 borradores: uno BREVE y directo, y otro más DETALLADO/explicativo.
Responde SOLO con JSON conforme al esquema."""


class TicketReplyService:
    @staticmethod
    def can_use(user):
        return getattr(settings, "AI_REPLY_ASSIST_ENABLED", False) and is_agent(user) and ai.is_configured()

    @staticmethod
    def _language(ticket):
        lang = ticket.language or (ticket.brand.language if ticket.brand_id and getattr(ticket.brand, "language", None) else None)
        return normalize_language(lang) or "es"

    @staticmethod
    def _conversation(ticket):
        rows = (
            Comment.objects.filter(ticket=ticket, is_public=True)
            .select_related("user", "user__role")
            .order_by("created_at")[:MAX_COMMENTS]
        )
        lines = []
        for c in rows:
            who = "Agente" if (c.user and is_agent(c.user)) else "Cliente"
            body = (c.content or "").strip()[:MAX_COMMENT_CHARS]
            if body:
                lines.append(f"{who}: {body}")
        return lines

    @staticmethod
    def suggest(ticket, instruction="", k=None):
        """Devuelve dict {language, drafts:[{title,body}], similar:[{id,subject,score}], meta}."""
        k = k if k is not None else getattr(settings, "AI_SIMILAR_TICKETS_K", 3)
        lang = TicketReplyService._language(ticket)
        lang_name = {"es": "español", "en": "inglés"}.get(lang, "español")

        similar = SimilarTicketIndex.find_similar(ticket, k=k, resolved_only=True)
        similar_blocks = []
        similar_refs = []
        for sim_ticket, score in similar:
            excerpt = SimilarTicketIndex.resolution_excerpt(sim_ticket)
            similar_blocks.append(
                f"[Caso #{sim_ticket.id}] Asunto: {sim_ticket.subject}\nResolución: {excerpt}"
            )
            similar_refs.append({"id": sim_ticket.id, "subject": sim_ticket.subject, "score": round(score, 3)})

        convo = TicketReplyService._conversation(ticket)
        brand_name = ticket.brand.name if ticket.brand_id and ticket.brand else "soporte"

        user_parts = [
            f"TICKET #{ticket.id} — Asunto: {ticket.subject}",
            f"Descripción inicial:\n{(ticket.description or '').strip()[:3000]}",
        ]
        if convo:
            user_parts.append("Conversación (público):\n" + "\n".join(convo))
        if similar_blocks:
            user_parts.append("CASOS SIMILARES YA RESUELTOS:\n" + "\n\n".join(similar_blocks))
        else:
            user_parts.append("(No se han encontrado casos similares relevantes.)")
        if instruction.strip():
            user_parts.append(f"Instrucción del agente para esta respuesta: {instruction.strip()}")

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT.format(brand=brand_name, lang=lang, lang_name=lang_name)},
            {"role": "user", "content": "\n\n".join(user_parts)},
        ]

        parsed, meta = ai.chat_json(messages, json_schema=REPLY_SCHEMA, temperature=0.3, max_tokens=900)

        drafts = []
        for d in (parsed.get("drafts") or [])[:3]:
            body = (d.get("body") or "").strip()
            if body:
                drafts.append({"title": (d.get("title") or "Borrador").strip()[:120], "body": body})

        record_metric("ai.reply_assist.success", labels={"ticket_id": ticket.id, "drafts": len(drafts)})
        return {
            "language": parsed.get("language") or lang,
            "drafts": drafts,
            "similar": similar_refs,
            "meta": {"model": meta.get("model"), "latency_ms": meta.get("latency_ms"), "prompt_version": PROMPT_VERSION},
        }
