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


def can_view_ticket(user, ticket):
    if is_agent(user):
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
    return can_view_ticket(user, ticket)


def can_manage_users(user):
    return is_admin(user)


def agent_user_filter():
    q = Q(is_superuser=True)
    for name in AGENT_ROLE_NAMES:
        q |= Q(role__role_name__iexact=name)
    return q
