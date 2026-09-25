from app.models import Notification, User


class NotificationService:
    @staticmethod
    def notify_ticket_users(ticket, message, actor=None):
        seen = set()
        targets = []
        for user in (ticket.requester, ticket.assignee):
            if user and user != actor and user.id not in seen:
                seen.add(user.id)
                targets.append(user)

        created = [
            Notification.objects.create(user=user, ticket=ticket, message=message)
            for user in targets
        ]
        if ticket.assignee_id is None and ticket.assigned_group_id:
            # Sin asignado no hay agente al que avisar y la respuesta del
            # cliente se quedaba sin dueño: la recoge el grupo enrutado.
            created += NotificationService.notify_group(
                ticket, message, ticket.assigned_group, actor=actor, exclude_ids=seen,
            )
        return created

    @staticmethod
    def notify_users(ticket, message, users, actor=None):
        seen = set()
        created = []
        for user in users:
            if user and user != actor and user.id not in seen:
                seen.add(user.id)
                created.append(Notification.objects.create(user=user, ticket=ticket, message=message))
        return created

    @staticmethod
    def notify_group(ticket, message, group, actor=None, exclude_ids=()):
        """Notifica a todos los miembros activos de un grupo (eventos de SLA:
        las reglas de asignación ya enrutan por grupo+usuario; esto hace que
        los avisos lleguen al grupo entero, no solo al asignado)."""
        if group is None:
            return []
        exclude = set(exclude_ids or ())
        if actor is not None:
            exclude.add(actor.id)
        members = User.objects.filter(is_active=True, group=group).exclude(id__in=exclude)
        return [
            Notification.objects.create(user=user, ticket=ticket, message=message)
            for user in members
        ]

    @staticmethod
    def unread_for_user(user, limit=20):
        return Notification.objects.filter(user=user, read=False).order_by("-created_at")[:limit]

    @staticmethod
    def unread_count(user):
        return Notification.objects.filter(user=user, read=False).count()

    @staticmethod
    def mark_ticket_read(user, ticket_id):
        return Notification.objects.filter(
            user=user, ticket_id=ticket_id, read=False,
        ).update(read=True)
