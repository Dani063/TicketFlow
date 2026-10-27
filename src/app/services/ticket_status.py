"""Transiciones de estado de un ticket.

Publicar un comentario y cambiar solo el estado aplican la misma transición
(relojes, TicketEvent); vive aquí para que no diverjan.
"""

from app.api import APIValidationError
from app.models import TicketEvent
from app.permissions import can_update_ticket


STATUS_VALUES = ("open", "pending", "resolved", "closed")


def apply_status(ticket, actor, new_status, now):
    """Cambia el estado de `ticket`, ya bloqueado por el llamador, y deja el evento.

    Devuelve False sin tocar nada si `new_status` viene vacío o coincide con el
    actual.
    """
    if not isinstance(new_status, str):
        raise APIValidationError("invalid_status", "Estado no valido.", status=400)
    new_status = new_status.strip().lower()
    if not new_status or new_status == ticket.status:
        return False
    if not can_update_ticket(actor, ticket):
        raise APIValidationError("forbidden_status_change", "No puedes cambiar el estado.", status=403)
    if new_status not in STATUS_VALUES:
        raise APIValidationError("invalid_status", "Estado no valido.", status=400)
    prev = ticket.status
    ticket.status = new_status
    ticket.closed_at = now if new_status == "closed" else ticket.closed_at
    # Ver TicketService.update_ticket: resolved_at es el reloj de la encuesta
    # de satisfacción y se refresca en cada entrada en 'resolved'.
    if new_status == "resolved":
        ticket.resolved_at = now
    elif new_status == "closed" and not ticket.resolved_at:
        ticket.resolved_at = now
    ticket.updated_at = now
    ticket.save(update_fields=["status", "updated_at", "closed_at", "resolved_at"])
    TicketEvent.objects.create(
        ticket=ticket,
        actor=actor,
        field_name="status",
        old_value=prev,
        new_value=new_status,
        created_at=now,
    )
    return True
