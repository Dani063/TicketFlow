"""#21378: rol de solo lectura. Ve lo que ve un agente y no modifica nada."""

import json

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import override_settings
from django.urls import reverse

from app.models import Comment, Role, Ticket, User
from app.permissions import can_comment_ticket, can_view_ticket, is_agent, is_readonly
from app.tests import _BaseFixture


@override_settings(ALLOWED_HOSTS=["testserver"], OUTBOUND_EMAIL_ENABLED=False)
class ReadOnlyRoleTests(_BaseFixture):
    def setUp(self):
        super().setUp()
        self.reader = User.objects.create_user("lector@example.com", "Lector", "secret")
        self.reader.role = Role.objects.get(role_name="Solo lectura")
        self.reader.save(update_fields=["role"])
        self.ticket = self.make_ticket(brand=self.brand_a, assignee=self.agent)
        Comment.objects.create(ticket=self.ticket, user=self.agent, content="Nota interna secreta", is_public=False)
        self.client.force_login(self.reader)

    def post_json(self, url, data):
        return self.client.post(url, data=json.dumps(data), content_type="application/json")

    def test_permissions(self):
        self.assertTrue(is_readonly(self.reader))
        self.assertFalse(is_agent(self.reader))
        self.assertTrue(can_view_ticket(self.reader, self.ticket))
        self.assertFalse(can_comment_ticket(self.reader, self.ticket))
        self.assertFalse(is_readonly(self.agent))

    def test_sees_every_ticket_in_the_queues(self):
        other = self.make_ticket(brand=self.brand_a, requester=self.admin, assignee=None)

        response = self.client.get("/tickets/filter/", {"view": "all_unsolved_no_tareas", "page_size": 50})

        self.assertEqual(response.status_code, 200)
        self.assertCountEqual([t["id"] for t in response.json()["tickets"]], [self.ticket.id, other.id])

    def test_ticket_page_shows_internal_notes_in_read_only_mode(self):
        response = self.client.get(reverse("create_ticket") + f"?id={self.ticket.id}")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["can_use_internal"])
        self.assertFalse(response.context["can_edit"])
        content = response.content.decode()
        self.assertIn("Nota interna secreta", content)
        self.assertIn('data-locked="readonly"', content)
        self.assertNotIn('id="take-it-btn"', content)
        self.assertNotIn('id="merge-btn"', content)

    def test_live_refresh_includes_internal_notes(self):
        response = self.client.get(reverse("ticket_live", args=[self.ticket.id]), {"after": 0})

        self.assertEqual(response.status_code, 200)
        self.assertIn("Nota interna secreta", json.dumps(response.json()))

    def test_cannot_write(self):
        add_comment = reverse("add_comment", args=[self.ticket.id])
        attempts = {
            "comentario": self.post_json(add_comment, {"content": "hola"}),
            "estado": self.post_json(add_comment, {"new_status": "pending"}),
            "campos": self.client.post(
                reverse("create_ticket") + f"?id={self.ticket.id}",
                {"subject": "x", "status": "pending", "producto": str(self.product.id)},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            ),
            "crear": self.client.post(
                reverse("create_ticket"),
                {"subject": "Nuevo", "message": "hola", "producto": str(self.product.id)},
                HTTP_X_REQUESTED_WITH="XMLHttpRequest",
            ),
            "asignarse": self.client.post(reverse("take_ticket", args=[self.ticket.id])),
            "borrar": self.post_json(reverse("bulk_delete"), {"ids": [self.ticket.id]}),
            "adjunto": self.client.post(
                reverse("upload_attachment", args=[self.ticket.id]),
                {"file": SimpleUploadedFile("a.txt", b"hola", content_type="text/plain")},
            ),
        }
        for name, response in attempts.items():
            with self.subTest(name):
                self.assertEqual(response.status_code, 403, response.content[:200])
        self.ticket.refresh_from_db()
        self.assertEqual((self.ticket.status, self.ticket.subject, self.ticket.assignee_id, self.ticket.is_deleted),
                         ("open", "Ticket", self.agent.id, False))
        self.assertEqual(Comment.objects.filter(ticket=self.ticket).count(), 1)
        self.assertFalse(Ticket.objects.filter(subject="Nuevo").exists())

    def test_new_ticket_form_is_forbidden(self):
        self.assertEqual(self.client.get(reverse("create_ticket")).status_code, 403)

    def test_reporting_and_customers_are_readable(self):
        self.assertEqual(self.client.get(reverse("reporting")).status_code, 200)
        self.assertEqual(self.client.get(reverse("customers_list")).status_code, 200)

    def test_lists_hide_bulk_actions_and_new_ticket_button(self):
        for name in ("tickets_list", "home"):
            with self.subTest(name):
                content = self.client.get(reverse(name)).content.decode()
                self.assertNotIn('id="ticketActionBar"', content)
                self.assertNotIn('class="add-tab"', content)
                self.assertIn("window.TF_CAN_CREATE_TICKETS = false", content)

    def test_agents_keep_bulk_actions(self):
        self.client.force_login(self.agent)
        content = self.client.get(reverse("tickets_list")).content.decode()
        self.assertIn('id="ticketActionBar"', content)
        self.assertIn("window.TF_CAN_CREATE_TICKETS = true", content)

    def test_role_is_protected_in_the_admin_panel(self):
        self.client.force_login(self.admin)
        roles = self.client.get(reverse("admin_roles_api")).json()
        rows = roles if isinstance(roles, list) else roles.get("roles") or roles.get("data") or []
        row = next(r for r in rows if r["role_name"] == "Solo lectura")
        self.assertTrue(row["protected"])
