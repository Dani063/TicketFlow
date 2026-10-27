import base64
from datetime import timedelta
from email import message_from_bytes, policy as email_policy
from unittest.mock import patch

from django.test import override_settings
from django.utils import timezone

from app.models import InboundEmailLog, OutboundEmailLog
from app.services.comments import CommentService
from app.services.email_outbound import OutboundEmailService
from app.services.email_templates import ResponseTemplateService
from app.tests import _BaseFixture


@override_settings(OUTBOUND_EMAIL_ENABLED=True, OUTBOUND_AGENT_REPLY_ENABLED=True)
class EmailThreadTests(_BaseFixture):
    """#21389: las respuestas siguen el hilo del correo del cliente."""

    def email_ticket(self, subject="Consulta de grabaciones", brand=None):
        ticket = self.make_ticket(subject=subject, brand=brand or self.brand_a, channel="email")
        now = timezone.now()
        for minutes, internet_id in ((30, "<first@cliente.example>"), (5, "<second@cliente.example>")):
            InboundEmailLog.objects.create(
                brand=ticket.brand, ticket=ticket, message_id=f"graph-{internet_id}",
                internet_message_id=internet_id, graph_received_at=now - timedelta(minutes=minutes),
            )
        return ticket

    def reply(self, ticket, text="Lo revisamos"):
        CommentService.add_comment(ticket, self.agent, text, is_public=True)
        return OutboundEmailLog.objects.filter(ticket=ticket, template_key="agent_reply").latest("id")

    def deliver_graph(self, log):
        with patch("app.services.msgraph.get_graph_token", return_value="tok"), \
             patch("requests.post") as post:
            post.return_value.raise_for_status.return_value = None
            provider_id = OutboundEmailService.deliver(log)
        raw = base64.b64decode(post.call_args.kwargs["data"])
        return provider_id, message_from_bytes(raw, policy=email_policy.default)

    def test_thread_subject_drops_reply_prefixes_and_ticket_tag(self):
        ticket = self.make_ticket(subject="RE: FW: Consulta [Ticket #12]")
        self.assertEqual(ResponseTemplateService.thread_subject(ticket), "Consulta")

    def test_reply_subject_and_headers_follow_the_customer_thread(self):
        ticket = self.email_ticket()

        log = self.reply(ticket)

        self.assertEqual(log.subject, f"RE: Consulta de grabaciones [Ticket #{ticket.id}]")
        thread = log.payload["thread"]
        self.assertEqual(thread["in_reply_to"], "<second@cliente.example>")
        self.assertEqual(thread["references"], ["<first@cliente.example>", "<second@cliente.example>"])
        self.assertRegex(thread["message_id"], rf"^<tf\.{ticket.id}\.[0-9a-f]{{32}}@comuny\.example>$")

    def test_subject_is_not_html_escaped(self):
        ticket = self.email_ticket(subject="Factura & pagos <urgente>")

        log = self.reply(ticket, text="Revisado: 3 < 5 & ok")

        self.assertEqual(log.subject, f"RE: Factura & pagos <urgente> [Ticket #{ticket.id}]")
        self.assertIn("3 < 5 & ok", log.payload["body_text"])

    def test_graph_sends_mime_with_thread_headers(self):
        ticket = self.email_ticket()
        log = self.reply(ticket)

        provider_id, sent = self.deliver_graph(log)

        self.assertEqual(provider_id, log.payload["thread"]["message_id"])
        self.assertEqual(sent["Message-ID"], log.payload["thread"]["message_id"])
        self.assertEqual(sent["In-Reply-To"], "<second@cliente.example>")
        self.assertEqual(sent["References"], "<first@cliente.example> <second@cliente.example>")
        self.assertEqual(sent["X-Auto-Response-Suppress"], "All")
        self.assertEqual(sent["To"], self.customer.email)
        self.assertIn("Lo revisamos", sent.get_body(preferencelist=("plain",)).get_content())

    def test_next_reply_also_references_our_previous_one(self):
        ticket = self.email_ticket()
        first = self.reply(ticket, text="Primera")
        OutboundEmailLog.objects.filter(pk=first.pk).update(
            status=OutboundEmailLog.STATUS_SENT, sent_at=timezone.now(),
        )

        second = self.reply(ticket, text="Segunda")

        thread = second.payload["thread"]
        self.assertEqual(thread["references"][-1], first.payload["thread"]["message_id"])
        self.assertEqual(thread["in_reply_to"], "<second@cliente.example>")

    def test_ses_sends_raw_mime_with_thread_and_anti_loop_headers(self):
        ticket = self.email_ticket(brand=self.brand_b)
        log = self.reply(ticket)

        with patch("boto3.client") as boto:
            boto.return_value.send_email.return_value = {"MessageId": "ses-1"}
            self.assertEqual(OutboundEmailService.deliver(log), "ses-1")

        kwargs = boto.return_value.send_email.call_args.kwargs
        sent = message_from_bytes(kwargs["Content"]["Raw"]["Data"], policy=email_policy.default)
        self.assertEqual(sent["In-Reply-To"], "<second@cliente.example>")
        self.assertEqual(sent["Auto-Submitted"], "auto-generated")
        self.assertEqual(kwargs["Destination"]["ToAddresses"], [self.customer.email])

    def test_ticket_without_inbound_mail_has_no_reply_headers(self):
        ticket = self.make_ticket(brand=self.brand_a, channel="web")

        log = self.reply(ticket)

        self.assertNotIn("in_reply_to", log.payload["thread"])
        _, sent = self.deliver_graph(log)
        self.assertIsNone(sent["In-Reply-To"])
        self.assertTrue(sent["Message-ID"].startswith(f"<tf.{ticket.id}."))


@override_settings(OUTBOUND_EMAIL_ENABLED=True, OUTBOUND_AGENT_REPLY_ENABLED=True,
                   OUTBOUND_TICKET_CREATED_ENABLED=True)
class CreationAcknowledgementTests(_BaseFixture):
    """#21380: acuse para quien abre el ticket, sin duplicar la respuesta del agente."""

    def create(self, actor, **extra):
        from app.services.tickets import TicketService

        payload = {"subject": "Alta de extensión", "description": "", "status": "open",
                   "requester_id": self.customer.id, "brand": self.brand_a, "channel": "web"}
        payload.update(extra)
        ticket = TicketService.create_ticket(actor, payload)
        return list(OutboundEmailLog.objects.filter(ticket=ticket).values_list("template_key", flat=True))

    def test_agent_ticket_with_public_message_sends_only_the_reply(self):
        self.assertEqual(self.create(self.agent, content="Te damos de alta hoy"), ["agent_reply"])

    def test_agent_ticket_with_internal_note_still_acknowledges(self):
        self.assertEqual(self.create(self.agent, content="Nota", is_public=False), ["ticket_created"])

    def test_customer_ticket_is_acknowledged_in_its_thread(self):
        keys = self.create(self.customer, content="Necesito una extensión", channel="email",
                           via_channel="email", email_message_id="m-1")
        self.assertEqual(keys, ["ticket_created"])
        log = OutboundEmailLog.objects.get(template_key="ticket_created")
        self.assertTrue(log.subject.startswith("RE: Alta de extensión [Ticket #"))
