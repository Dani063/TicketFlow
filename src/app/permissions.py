from django.db.models import Q


ADMIN_ROLE_NAMES = {"admin", "administrator", "administrador"}
AGENT_ROLE_NAMES = {
    "agent",
    "agente",
    "support",
    "soporte",
    "supervisor",
    "manager",
    "team lead",
    "lead",
} | ADMIN_ROLE_NAMES
END_USER_ROLE_NAMES = {"end user", "end-user", "cliente", "customer"}
# Rol de solo lectura (#21378): ve lo mismo que un agente (tickets, notas
# internas, clientes, reporting) pero no modifica nada. is_agent sigue
# significando «puede escribir», así que el reparto, el SLA y el reporting por
# agente no lo cuentan como agente.
READONLY_ROLE_NAMES = {"solo lectura", "solo-lectura", "lectura", "read only", "read-only", "readonly", "viewer"}


def role_name(user):
    role = getattr(user, "role", None)
    return ((getattr(role, "role_name", "") or "").strip().lower())


def is_admin(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return role_name(user) in ADMIN_ROLE_NAMES


def is_agent(user):
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False):
        return True
    return role_name(user) in AGENT_ROLE_NAMES


def is_end_user(user):
    return role_name(user) in END_USER_ROLE_NAMES


def is_readonly(user):
    if not getattr(user, "is_authenticated", False) or is_agent(user):
        return False
    return role_name(user) in READONLY_ROLE_NAMES


def can_view_all_tickets(user):
    """Ve todos los tickets y las notas internas: agentes y solo lectura."""
    return is_agent(user) or is_readonly(user)


def can_view_ticket(user, ticket):
    if can_view_all_tickets(user):
        return True
    if not getattr(user, "is_authenticated", False):
        return False
    return (
        ticket.requester_id == user.id
        or ticket.created_by_id == user.id
        or ticket.ccs.filter(id=user.id).exists()
    )


def can_update_ticket(user, ticket):
    return is_agent(user)


def can_comment_ticket(user, ticket):
    return not is_readonly(user) and can_view_ticket(user, ticket)


def can_manage_users(user):
    return is_admin(user)


def can_manage_help_content(user):
    """Help content is editable by active, authenticated support staff."""
    return bool(getattr(user, "is_active", False) and is_agent(user))


def agent_user_filter():
    q = Q(is_superuser=True)
    for name in AGENT_ROLE_NAMES:
        q |= Q(role__role_name__iexact=name)
    return q
