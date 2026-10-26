from unittest.mock import patch

from django.test import override_settings

from app.models import OutboundEmailLog, Ticket, User
from app.services.comments import CommentService
from app.services.email_ingestion import EmailIngestionService
from app.services.email_outbound import OutboundEmailService
from app.tests import _BaseFixture


@override_settings(OUTBOUND_EMAIL_ENABLED=True, OUTBOUND_AGENT_REPLY_ENABLED=True,
                   OUTBOUND_TICKET_CREATED_ENABLED=True)
class EmailCCTests(_BaseFixture):
    def test_creation_and_reply_preserve_valid_ccs(self):
        def recipient(email):
            return {"emailAddress": {"address": email, "name": "Copia"}}

        message = {
            "id": "cc-inbound", "conversationId": "cc-conversation", "subject": "Copias",
            "from": recipient(self.customer.email),
            "body": {"contentType": "text", "content": "Ayuda"},
            "ccRecipients": [recipient(" COPY@example.com "), recipient("copy@example.com"),
                             recipient(self.customer.email), recipient(self.brand_b.support_email),
                             recipient("invalid"), None],
        }
        self.assertEqual(EmailIngestionService.process_message(message, self.brand_a), "ticket_created")
        ticket = Ticket.objects.get(email_message_id=message["id"])
        self.assertEqual(list(ticket.ccs.values_list("email", flat=True)), ["copy@example.com"])
        message.update(id="cc-reply", ccRecipients=[recipient("second@example.com")])
        self.assertEqual(EmailIngestionService.process_message(message, self.brand_a), "comment_added")
        self.assertEqual(set(ticket.ccs.values_list("email", flat=True)),
                         {"copy@example.com", "second@example.com"})
        self.assertEqual(EmailIngestionService.process_message(message, self.brand_a), "skip_dup")
        self.assertEqual(ticket.ccs.count(), 2)

    def test_public_reply_snapshots_ccs_and_delivers_via_graph_and_ses(self):
        ticket = self.make_ticket(brand=self.brand_a)
        copy = User.objects.create_user("copy@example.com", "Copia", "secret")
        own = User.objects.create_user(self.brand_b.support_email, "Buzon", "secret")
        noreply = User.objects.create_user("no-reply@example.com", "Automatico", "secret")
        ticket.ccs.add(copy, own, noreply, self.customer)
        comment, _ = CommentService.add_comment(ticket, self.agent, "Respuesta", is_public=True)
        log = OutboundEmailLog.objects.get(ticket=ticket, template_key="agent_reply")
        self.assertEqual(log.payload["cc_emails"], [copy.email])
        ticket.ccs.clear()
        with patch("app.services.msgraph.get_graph_token", return_value="tok"), patch("requests.post") as post:
            OutboundEmailService.deliver(log)
        self.assertEqual(post.call_args.kwargs["json"]["message"]["ccRecipients"],
                         [{"emailAddress": {"address": copy.email}}])
        log.brand = self.brand_b
        with patch("boto3.client") as boto:
            OutboundEmailService.deliver(log)
        self.assertEqual(boto.return_value.send_email.call_args.kwargs["Destination"]["CcAddresses"],
                         [copy.email])
        self.assertEqual(OutboundEmailService.queue_agent_reply(ticket, comment).id, log.id)

    def test_private_notes_do_not_send_email(self):
        ticket = self.make_ticket(brand=self.brand_a)
        ticket.ccs.add(self.admin)
        CommentService.add_comment(ticket, self.agent, "Interno", is_public=False)
        self.assertFalse(OutboundEmailLog.objects.filter(ticket=ticket).exists())

    def test_confirmations_and_surveys_do_not_copy_recipients(self):
        ticket = self.make_ticket(brand=self.brand_a)
        log = OutboundEmailService.queue_ticket_confirmation(ticket)
        log.payload["cc_emails"] = ["copy@example.com"]
        self.assertEqual(OutboundEmailService._cc_emails(log), [])
        log.template_key = "satisfaction_survey"
        self.assertEqual(OutboundEmailService._cc_emails(log), [])

    def test_sweep_message_fetch_requests_cc_field(self):
        from app.services import msgraph_mail

        with patch("app.services.msgraph_mail.requests.request") as request:
            request.return_value.status_code = 200
            request.return_value.json.return_value = {"id": "message", "ccRecipients": []}
            msgraph_mail.get_message(self.brand_a.support_email, "message", {})
        self.assertIn("ccRecipients", request.call_args.kwargs["params"]["$select"].split(","))
