import json
from datetime import timedelta
from unittest.mock import patch

from django.test import override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import Comment, OutboundEmailLog, Ticket, TicketEvent, User
from app.services.comments import CommentService
from app.services.email_ingestion import EmailIngestionService
from app.services.reporting_metrics import _is_resolution
from app.services.ticket_status import close_expired_on_hold
from app.services.tickets import TicketService
from app.tests import _BaseFixture


@override_settings(OUTBOUND_EMAIL_ENABLED=True, OUTBOUND_AGENT_REPLY_ENABLED=True,
                   OUTBOUND_TICKET_ON_HOLD_ENABLED=True, TICKET_ON_HOLD_CLOSE_HOURS=72)
class OnHoldLifecycleTests(_BaseFixture):
    """#21384: on_hold es el cierre de los agentes; closed lo pone el sistema."""

    def setUp(self):
        super().setUp()
        self.client.force_login(self.agent)

    def post_status(self, ticket, status):
        return self.client.post(
            reverse("add_comment", args=[ticket.id]),
            data=json.dumps({"new_status": status}),
            content_type="application/json",
        )

    def save_fields(self, ticket, **data):
        data.setdefault("subject", ticket.subject)
        data.setdefault("producto", str(self.product.id))
        return self.client.post(
            reverse("create_ticket") + f"?id={ticket.id}",
            data=data,
            HTTP_X_REQUESTED_WITH="XMLHttpRequest",
        )

    def put_on_hold(self, ticket, at):
        with patch("django.utils.timezone.now", return_value=at):
            TicketService.change_status(self.agent, ticket.id, "on_hold")
        ticket.refresh_from_db()
        return ticket

    def test_agents_cannot_close_by_hand(self):
        ticket = self.make_ticket(brand=self.brand_a)

        response = self.post_status(ticket, "closed")

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"]["code"], "invalid_status")

    def test_on_hold_starts_the_clock_and_sends_the_notice(self):
        ticket = self.make_ticket(brand=self.brand_a)

        response = self.post_status(ticket, "on_hold")

        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "on_hold")
        self.assertIsNotNone(ticket.on_hold_at)
        self.assertIsNotNone(ticket.resolved_at)
        log = OutboundEmailLog.objects.get(ticket=ticket)
        self.assertEqual(log.template_key, "ticket_on_hold")
        self.assertEqual(log.status, OutboundEmailLog.STATUS_QUEUED)
        self.assertIn("72 horas", log.payload["body_text"])

    def test_reply_with_on_hold_carries_the_notice_in_the_same_email(self):
        ticket = self.make_ticket(brand=self.brand_a)

        CommentService.add_comment(ticket, self.agent, "Ya está corregido", new_status="on_hold")

        logs = list(OutboundEmailLog.objects.filter(ticket=ticket))
        self.assertEqual([log.template_key for log in logs], ["agent_reply"])
        self.assertIn("Ya está corregido", logs[0].payload["body_text"])
        self.assertIn("72 horas", logs[0].payload["body_text"])
        self.assertIn("72 horas", logs[0].payload["body_html"])

    def test_plain_reply_has_no_closing_notice(self):
        ticket = self.make_ticket(brand=self.brand_a)

        CommentService.add_comment(ticket, self.agent, "Lo revisamos", new_status="pending")

        log = OutboundEmailLog.objects.get(ticket=ticket)
        self.assertNotIn("72 horas", log.payload["body_text"])

    @override_settings(OUTBOUND_TICKET_ON_HOLD_ENABLED=False)
    def test_notice_switch_off_keeps_the_reply_without_it(self):
        ticket = self.make_ticket(brand=self.brand_a)

        CommentService.add_comment(ticket, self.agent, "Cerramos", new_status="on_hold")

        log = OutboundEmailLog.objects.get(ticket=ticket)
        self.assertEqual(log.template_key, "agent_reply")
        self.assertNotIn("72 horas", log.payload["body_text"])

    def test_commenting_interrupts_on_hold(self):
        ticket = self.put_on_hold(self.make_ticket(brand=self.brand_a), timezone.now())

        CommentService.add_comment(ticket, self.agent, "Nota", is_public=False)

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "open")
        self.assertIsNone(ticket.on_hold_at)

    def test_choosing_on_hold_again_restarts_the_wait(self):
        start = timezone.now() - timedelta(hours=10)
        ticket = self.put_on_hold(self.make_ticket(brand=self.brand_a), start)

        CommentService.add_comment(ticket, self.agent, "Un detalle más", new_status="on_hold")

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "on_hold")
        self.assertGreater(ticket.on_hold_at, start)
        statuses = list(
            TicketEvent.objects.filter(ticket=ticket, field_name="status")
            .order_by("id").values_list("old_value", "new_value")
        )
        self.assertEqual(statuses[-2:], [("on_hold", "open"), ("open", "on_hold")])

    def test_editing_fields_interrupts_on_hold(self):
        ticket = self.put_on_hold(self.make_ticket(brand=self.brand_a), timezone.now())
        other = User.objects.create_user("agent3@example.com", "Agent Three", "secret")

        response = self.save_fields(ticket, status="open", asignado=str(other.id))

        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.assignee_id), ("open", other.id))

    def test_saving_fields_without_status_keeps_it(self):
        ticket = self.make_ticket(brand=self.brand_a, status="pending")

        response = self.save_fields(ticket, subject="Asunto nuevo")

        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual((ticket.subject, ticket.status), ("Asunto nuevo", "pending"))

    def test_system_closes_after_the_wait(self):
        now = timezone.now()
        expired = self.put_on_hold(self.make_ticket(brand=self.brand_a), now - timedelta(hours=73))
        waiting = self.put_on_hold(self.make_ticket(brand=self.brand_a), now - timedelta(hours=71))

        self.assertEqual(close_expired_on_hold(now=now), [expired.id])

        expired.refresh_from_db()
        waiting.refresh_from_db()
        self.assertEqual(expired.status, "closed")
        self.assertIsNotNone(expired.closed_at)
        self.assertEqual(waiting.status, "on_hold")
        event = TicketEvent.objects.filter(ticket=expired, field_name="status").latest("id")
        self.assertEqual((event.actor_id, event.new_value), (None, "closed"))

    def test_closed_ticket_rejects_every_change(self):
        ticket = self.make_ticket(brand=self.brand_a, status="closed")

        comment = self.client.post(
            reverse("add_comment", args=[ticket.id]),
            data=json.dumps({"content": "hola"}), content_type="application/json",
        )
        status = self.post_status(ticket, "open")
        fields = self.save_fields(ticket, subject="x", status="open")

        for response in (comment, status, fields):
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json()["error"]["code"], "ticket_closed")
        ticket.refresh_from_db()
        self.assertEqual((ticket.status, ticket.subject), ("closed", "Ticket"))

    def test_sla_pause_pushes_the_deadlines(self):
        ticket = self.make_ticket(brand=self.brand_a)
        due = timezone.now() + timedelta(hours=1)
        Ticket.objects.filter(pk=ticket.pk).update(resolution_due_at=due, due_at=due)
        ticket = self.put_on_hold(ticket, timezone.now() - timedelta(hours=5))

        CommentService.add_comment(ticket, self.agent, "Seguimos", is_public=False)

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "open")
        self.assertGreaterEqual(ticket.sla_paused_seconds, 5 * 3600)
        self.assertAlmostEqual((ticket.resolution_due_at - due).total_seconds(), 5 * 3600, delta=60)
        self.assertEqual(ticket.due_at, ticket.resolution_due_at)

    def test_reporting_counts_on_hold_as_solved(self):
        self.assertTrue(_is_resolution("open", "on_hold"))
        self.assertFalse(_is_resolution("resolved", "on_hold"))
        self.assertFalse(_is_resolution("on_hold", "closed"))


class OnHoldInboundEmailTests(_BaseFixture):
    def message(self, msg_id, conversation="conv-1", subject="Consulta"):
        return {
            "id": msg_id, "conversationId": conversation, "subject": subject,
            "from": {"emailAddress": {"address": self.customer.email, "name": "Customer"}},
            "body": {"contentType": "text", "content": "Hola"},
        }

    def ingest(self, msg_id, **kwargs):
        return EmailIngestionService.process_message(self.message(msg_id, **kwargs), self.brand_a)

    def test_reply_during_on_hold_reopens_the_same_ticket(self):
        self.assertEqual(self.ingest("m-1"), "ticket_created")
        ticket = Ticket.objects.get(email_message_id="m-1")
        TicketService.change_status(self.agent, ticket.id, "on_hold")

        self.assertEqual(self.ingest("m-2"), "comment_added")

        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "open")
        self.assertEqual(Ticket.objects.count(), 1)

    def test_reply_to_a_closed_ticket_opens_a_follow_up(self):
        self.ingest("m-1")
        closed = Ticket.objects.get(email_message_id="m-1")
        Ticket.objects.filter(pk=closed.pk).update(status="closed")

        self.assertEqual(self.ingest("m-2"), "ticket_created")

        follow_up = Ticket.objects.get(email_message_id="m-2")
        self.assertNotEqual(follow_up.id, closed.id)
        event = TicketEvent.objects.get(ticket=follow_up, field_name="follow_up_of")
        self.assertEqual(event.new_value, str(closed.id))
        # La siguiente respuesta de la misma conversación va al seguimiento.
        self.assertEqual(self.ingest("m-3"), "comment_added")
        self.assertEqual(Comment.objects.filter(ticket=follow_up).count(), 2)

    def test_reply_to_a_merged_ticket_goes_to_its_target(self):
        self.ingest("m-1")
        source = Ticket.objects.get(email_message_id="m-1")
        target = self.make_ticket(brand=self.brand_a)
        Ticket.objects.filter(pk=source.pk).update(status="closed", merged_into=target)

        self.assertEqual(self.ingest("m-2"), "comment_added")

        self.assertTrue(Comment.objects.filter(ticket=target, email_message_id="m-2").exists())

    def test_reply_to_a_deleted_ticket_opens_a_new_one(self):
        self.ingest("m-1")
        deleted = Ticket.objects.get(email_message_id="m-1")
        Ticket.objects.filter(pk=deleted.pk).update(is_deleted=True)

        self.assertEqual(self.ingest("m-2"), "ticket_created")
        self.assertFalse(TicketEvent.objects.filter(field_name="follow_up_of").exists())
