"""Búsqueda de tickets similares por embeddings (coseno en memoria).

Interfaz SimilarTicketIndex: al volumen actual basta cargar los vectores y
calcular coseno en Python; si crece, se sustituye por OpenSearch k-NN sin tocar
los llamadores. Base del módulo Q3 de casos similares.
"""

import hashlib
import logging
import math

from app.models import Comment, Ticket, TicketEmbedding
from app.services import ai

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 4000
RESOLVED_STATUSES = ("resolved", "closed")


def _content_text(ticket):
    parts = [ticket.subject or "", ticket.description or ""]
    return "\n".join(p for p in parts if p).strip()[:MAX_TEXT_CHARS]


def _hash(text, model):
    return hashlib.sha256(f"{model}:{text}".encode("utf-8")).hexdigest()


def _cosine(a, b):
    if not a or not b or len(a) != len(b):
        return -1.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0 or nb == 0:
        return -1.0
    return dot / (na * nb)


class SimilarTicketIndex:
    @staticmethod
    def embed_ticket(ticket, force=False):
        """Calcula (o reutiliza) el embedding de un ticket. Devuelve el vector."""
        text = _content_text(ticket)
        if not text:
            return None
        model = ai._embedding_model()
        digest = _hash(text, model)
        existing = TicketEmbedding.objects.filter(ticket=ticket).first()
        if existing and not force and existing.content_hash == digest and existing.vector:
            return existing.vector

        vectors, model_name = ai.embed([text])
        vector = vectors[0]
        TicketEmbedding.objects.update_or_create(
            ticket=ticket,
            defaults={
                "vector": vector,
                "content_hash": _hash(text, model_name),
                "model_name": model_name,
                "dim": len(vector),
            },
        )
        return vector

    @staticmethod
    def backfill(queryset=None, force=False):
        """Embebe los tickets que falten (o todos si force). Devuelve el conteo."""
        qs = queryset if queryset is not None else Ticket.objects.filter(
            is_deleted=False, merged_into__isnull=True
        )
        done = 0
        for ticket in qs.iterator():
            try:
                if SimilarTicketIndex.embed_ticket(ticket, force=force) is not None:
                    done += 1
            except Exception:  # noqa: BLE001
                logger.exception("embed_ticket failed ticket=%s", ticket.id)
        return done

    @staticmethod
    def find_similar(ticket, k=3, resolved_only=True, same_brand=False):
        """Devuelve [(ticket, score)] de los k tickets más parecidos.

        resolved_only: solo casos ya resueltos/cerrados (los útiles como referencia).
        same_brand: restringe a la misma marca cuando el ticket tiene una.
        """
        query_vec = SimilarTicketIndex.embed_ticket(ticket)
        if not query_vec:
            return []

        candidates = (
            TicketEmbedding.objects.exclude(ticket_id=ticket.id)
            .select_related("ticket")
            .filter(ticket__is_deleted=False, ticket__merged_into__isnull=True)
        )
        if resolved_only:
            candidates = candidates.filter(ticket__status__in=RESOLVED_STATUSES)
        if same_brand and ticket.brand_id:
            candidates = candidates.filter(ticket__brand_id=ticket.brand_id)

        scored = []
        for emb in candidates.iterator():
            score = _cosine(query_vec, emb.vector)
            if score > 0:
                scored.append((emb.ticket, score))
        scored.sort(key=lambda pair: pair[1], reverse=True)
        return scored[:k]

    @staticmethod
    def resolution_excerpt(ticket, max_chars=600):
        """El comentario público más reciente del caso similar (su 'resolución'),
        o la descripción si no hay comentarios públicos."""
        last_public = (
            Comment.objects.filter(ticket=ticket, is_public=True)
            .order_by("-created_at")
            .values_list("content", flat=True)
            .first()
        )
        text = last_public or ticket.description or ""
        return text.strip()[:max_chars]
