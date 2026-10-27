import json

from django.urls import reverse

from app.models import Comment, Ticket, TicketEvent
from app.tests import _BaseFixture


class StatusOnlyUpdateTests(_BaseFixture):
    """#21393: poner un ticket en pendiente o resolverlo sin escribir comentario."""

    def post_status(self, ticket, status, user=None):
        self.client.force_login(user or self.agent)
        return self.client.post(
            reverse("add_comment", args=[ticket.id]),
            data=json.dumps({"new_status": status}),
            content_type="application/json",
        )

    def test_agent_changes_status_without_comment(self):
        ticket = self.make_ticket()

        response = self.post_status(ticket, "pending")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertTrue(data["status_only"])
        self.assertEqual(data["new_status"], "pending")
        self.assertEqual(data["ticket"]["status"], "pending")
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "pending")
        self.assertFalse(Comment.objects.filter(ticket=ticket).exists())
        event = TicketEvent.objects.get(ticket=ticket, field_name="status")
        self.assertEqual((event.old_value, event.new_value, event.actor_id), ("open", "pending", self.agent.id))

    def test_status_only_does_not_require_product(self):
        # Los tickets que entran por correo no traen producto; update_ticket lo exige.
        ticket = self.make_ticket(product_line=None)

        response = self.post_status(ticket, "resolved")

        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "resolved")
        self.assertIsNotNone(ticket.resolved_at)

    def test_same_status_without_comment_is_still_empty(self):
        ticket = self.make_ticket(status="pending")

        response = self.post_status(ticket, "pending")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "empty_comment")
        self.assertFalse(TicketEvent.objects.filter(ticket=ticket).exists())

    def test_end_user_cannot_change_status(self):
        ticket = self.make_ticket()

        response = self.post_status(ticket, "resolved", user=self.customer)

        self.assertEqual(response.status_code, 403)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "open")

    def test_unknown_status_is_rejected(self):
        ticket = self.make_ticket()

        response = self.post_status(ticket, "archivado")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_status")

    def test_comment_with_status_still_publishes_both(self):
        ticket = self.make_ticket()
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("add_comment", args=[ticket.id]),
            data=json.dumps({"content": "Queda pendiente del cliente", "new_status": "pending"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertNotIn("status_only", response.json())
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "pending")
        self.assertEqual(Comment.objects.filter(ticket=ticket).count(), 1)


class FieldSaveKeepsDescriptionTests(_BaseFixture):
    def test_saving_fields_without_message_keeps_description(self):
        ticket = self.make_ticket(description="Cuerpo original del correo")
        self.client.force_login(self.agent)

        response = self.client.post(
            reverse("create_ticket") + f"?id={ticket.id}",
            data={
                "subject": ticket.subject,
                "status": "pending",
                "producto": str(self.product.id),
                "asignado": str(self.agent.id),
            },
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

        self.assertEqual(response.status_code, 200)
        ticket = Ticket.objects.get(id=ticket.id)
        self.assertEqual(ticket.status, "pending")
        self.assertEqual(ticket.description, "Cuerpo original del correo")
