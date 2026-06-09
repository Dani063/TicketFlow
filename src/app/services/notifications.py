from app.models import Notification


class NotificationService:
    @staticmethod
    def notify_ticket_users(ticket, message, actor=None):
        seen = set()
        targets = []
        for user in (ticket.requester, ticket.assignee):
            if user and user != actor and user.id not in seen:
                seen.add(user.id)
                targets.append(user)

        return [
            Notification.objects.create(user=user, ticket=ticket, message=message)
            for user in targets
        ]

    @staticmethod
    def unread_for_user(user, limit=20):
        return Notification.objects.filter(user=user, read=False).order_by("-created_at")[:limit]
