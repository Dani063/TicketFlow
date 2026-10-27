"""Transiciones de estado de un ticket.

Estados: open, pending, resolved, on_hold y closed (#21384).

- ``on_hold`` es el cierre que ponen los agentes. Cuenta como resuelto y pausa
  el SLA. Si nadie toca el ticket en TICKET_ON_HOLD_CLOSE_HOURS, el sistema lo
  pasa a ``closed``; cualquier comentario o edición antes lo devuelve a open.
- ``closed`` solo lo pone el sistema (o una fusión) y ya no admite cambios: si
  el cliente vuelve a escribir, la ingesta abre un ticket nuevo.

Toda transición pasa por aquí para que relojes, pausa del SLA y TicketEvent no
diverjan entre publicar, guardar campos, la ingesta y el cierre automático.
"""

import logging
from datetime import timedelta

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from app.api import APIValidationError
from app.models import Ticket, TicketEvent
from app.permissions import can_update_ticket
from app.services.metrics import record_metric


logger = logging.getLogger(__name__)

UNSOLVED_STATUSES = ("open", "pending")
SOLVED_STATUSES = ("resolved", "on_hold", "closed")
STATUS_VALUES = UNSOLVED_STATUSES + SOLVED_STATUSES
# Los que puede elegir una persona: 'closed' solo lo pone el sistema.
AGENT_STATUS_VALUES = ("open", "pending", "resolved", "on_hold")


def on_hold_close_hours():
    return getattr(settings, "TICKET_ON_HOLD_CLOSE_HOURS", 72)


def ensure_editable(ticket):
    if ticket.is_deleted:
        raise APIValidationError("ticket_deleted", "El ticket está eliminado.", status=409)
    if ticket.status == "closed":
        raise APIValidationError(
            "ticket_closed", "El ticket está cerrado y ya no admite cambios.", status=409,
        )


def _resume_sla(ticket, now):
    """Sale de on_hold: el tiempo en espera no cuenta para el SLA.

    Desplaza los vencimientos vivos y acumula la pausa para que apply_policy,
    que recalcula desde created_at, llegue a las mismas fechas.
    """
    started = ticket.on_hold_at
    if not started or now <= started:
        return []
    paused = now - started
    ticket.sla_paused_seconds = (ticket.sla_paused_seconds or 0) + int(paused.total_seconds())
    fields = ["sla_paused_seconds"]
    # due_at también es la «Fecha límite» editable: solo se mueve si es la del SLA.
    if ticket.due_at and ticket.due_at == ticket.resolution_due_at:
        ticket.due_at += paused
        fields.append("due_at")
    for name in ("resolution_due_at", "first_response_due_at"):
        if getattr(ticket, name):
            setattr(ticket, name, getattr(ticket, name) + paused)
            fields.append(name)
    if len(fields) > 1 and ticket.sla_risk_notified_at:
        # El aviso de riesgo era para el vencimiento anterior.
        ticket.sla_risk_notified_at = None
        fields.append("sla_risk_notified_at")
    return fields


def transition(ticket, actor, new_status, now):
    """Aplica el cambio sin validar quién lo pide. Devuelve False si no cambia.

    `ticket` tiene que venir bloqueado (select_for_update) por el llamador.
    """
    prev = ticket.status
    if new_status == prev:
        return False
    ticket.status = new_status
    fields = ["status", "updated_at", "closed_at", "resolved_at", "on_hold_at"]
    if prev == "on_hold" and new_status != "closed":
        fields += _resume_sla(ticket, now)
    ticket.on_hold_at = now if new_status == "on_hold" else None
    # resolved_at es el reloj de la encuesta de satisfacción: se refresca en cada
    # resolución. Pasar a on_hold desde un estado sin resolver también lo es; desde
    # resolved no, la resolución ya contó.
    if new_status == "resolved" or (new_status == "on_hold" and prev not in SOLVED_STATUSES):
        ticket.resolved_at = now
    elif new_status == "closed":
        ticket.closed_at = now
        if not ticket.resolved_at:
            ticket.resolved_at = now
    ticket.updated_at = now
    ticket.save(update_fields=fields)
    TicketEvent.objects.create(
        ticket=ticket,
        actor=actor,
        field_name="status",
        old_value=prev,
        new_value=new_status,
        created_at=now,
    )
    return True


def apply_status(ticket, actor, new_status, now):
    """Cambio de estado pedido por una persona. Devuelve False si no cambia."""
    if not isinstance(new_status, str):
        raise APIValidationError("invalid_status", "Estado no valido.", status=400)
    new_status = new_status.strip().lower()
    if not new_status or new_status == ticket.status:
        return False
    if not can_update_ticket(actor, ticket):
        raise APIValidationError("forbidden_status_change", "No puedes cambiar el estado.", status=403)
    if new_status not in AGENT_STATUS_VALUES:
        raise APIValidationError("invalid_status", "Estado no valido.", status=400)
    return transition(ticket, actor, new_status, now)


def interrupt_on_hold(ticket, actor, now):
    """Comentar o editar un ticket en on_hold lo devuelve a open."""
    if ticket.status != "on_hold":
        return False
    return transition(ticket, actor, "open", now)


def queue_on_hold_notice(ticket):
    """Aviso al cliente de que el ticket se cerrará si no responde."""
    try:
        from app.services.email_outbound import OutboundEmailService

        OutboundEmailService.queue_on_hold_notice(ticket)
    except Exception:
        # El cambio de estado ya está hecho: un fallo de plantilla o de encolado
        # no debe deshacerlo.
        logger.exception("on_hold_notice_queue_failed", extra={"ticket_id": ticket.id})
        record_metric("email.outbound.on_hold_notice_queue_failed", labels={"ticket_id": ticket.id})


def close_expired_on_hold(now=None, limit=500):
    """Pasa a closed los tickets que llevan en on_hold el plazo completo."""
    now = now or timezone.now()
    cutoff = now - timedelta(hours=on_hold_close_hours())
    candidate_ids = list(
        Ticket.objects.filter(status="on_hold", on_hold_at__lte=cutoff, is_deleted=False)
        .order_by("on_hold_at")
        .values_list("id", flat=True)[:limit]
    )
    closed = []
    for ticket_id in candidate_ids:
        with transaction.atomic():
            # Se relee bloqueado: alguien pudo comentar entre la consulta y el cierre.
            ticket = (
                Ticket.objects.select_for_update()
                .filter(id=ticket_id, status="on_hold", on_hold_at__lte=cutoff)
                .first()
            )
            if ticket is not None and transition(ticket, None, "closed", now):
                closed.append(ticket_id)
    if closed:
        record_metric("tickets.on_hold_closed", labels={"count": len(closed)})
    logger.info("on_hold_close_scan", extra={"closed_count": len(closed)})
    return closed
