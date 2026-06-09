import json
from datetime import timedelta

from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from app.models import (
    AssignmentRule,
    AssignmentRuleMember,
    AutomationRule,
    Brand,
    Comment,
    InboundEmailLog,
    Notification,
    OperationalMetric,
    Role,
    SLAPolicy,
    Ticket,
    TicketEvent,
    TicketTag,
    User,
)
from app.permissions import is_agent
from app.services.email_ingestion import EmailIngestionService
from app.services.sla import SLAService


class TicketFlowBackendTests(TestCase):
    def setUp(self):
        self.end_role = Role.objects.create(role_name="End user")
        self.agent_role = Role.objects.create(role_name="agent")
        self.admin_role = Role.objects.create(role_name="admin")

        self.customer = User.objects.create_user(
            email="customer@example.com",
            name="Customer",
            password="secret",
        )
        self.customer.role = self.end_role
        self.customer.save(update_fields=["role"])

        self.agent = User.objects.create_user(
            email="agent@example.com",
            name="Agent",
            password="secret",
        )
        self.agent.role = self.agent_role
        self.agent.save(update_fields=["role"])

        self.agent2 = User.objects.create_user(
            email="agent2@example.com",
            name="Agent Two",
            password="secret",
        )
        self.agent2.role = self.agent_role
        self.agent2.save(update_fields=["role"])

        self.admin = User.objects.create_user(
            email="admin@example.com",
            name="Admin",
            password="secret",
        )
        self.admin.role = self.admin_role
        self.admin.save(update_fields=["role"])

    def make_ticket(self, **overrides):
        values = {
            "subject": "Ticket",
            "description": "desc",
            "status": "open",
            "priority": "normal",
            "requester": self.customer,
            "assignee": self.agent,
            "created_by": self.customer,
        }
        values.update(overrides)
        return Ticket.objects.create(**values)

    def test_permissions_are_explicit_and_ticket_visibility_is_scoped(self):
        ticket = self.make_ticket()
        other = User.objects.create_user("other@example.com", "Other", "secret")
        other.role = self.end_role
        other.save(update_fields=["role"])

        self.assertFalse(is_agent(self.customer))
        self.assertFalse(is_agent(other))
        self.assertTrue(is_agent(self.agent))

        response = self.client.get(reverse("ticket_detail_api", args=[ticket.id]))
        self.assertEqual(response.status_code, 401)

        self.client.force_login(other)
        response = self.client.get(reverse("ticket_detail_api", args=[ticket.id]))
        self.assertEqual(response.status_code, 403)

        self.client.force_login(self.customer)
        response = self.client.get(reverse("ticket_detail_api", args=[ticket.id]))
        self.assertEqual(response.status_code, 200)

    def test_admin_api_uses_401_403_and_csrf_protection(self):
        response = self.client.get(reverse("admin_users_api"))
        self.assertEqual(response.status_code, 401)

        self.client.force_login(self.customer)
        response = self.client.get(reverse("admin_users_api"))
        self.assertEqual(response.status_code, 403)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.admin)
        response = csrf_client.post(
            reverse("admin_users_api"),
            data=json.dumps({"name": "No CSRF", "email": "csrf@example.com"}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 403)

    def test_sso_complete_accepts_csrf_from_callback(self):
        csrf_client = Client(enforce_csrf_checks=True)
        response = csrf_client.get(reverse("sso_callback"))
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'name="csrf-token"', response.content)
        token = csrf_client.cookies["csrftoken"].value

        response = csrf_client.post(
            reverse("sso_complete"),
            data=json.dumps({"email": "sso@example.com", "name": "SSO User"}),
            content_type="application/json",
            HTTP_X_CSRFTOKEN=token,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(email="sso@example.com").exists())

    def test_create_ticket_is_validated_atomic_and_emits_sla_events_notifications(self):
        SLAPolicy.objects.create(
            name="Normal SLA",
            priority="normal",
            first_response_minutes=30,
            resolution_minutes=120,
        )
        rule = AssignmentRule.objects.create(name="SLA assignment")
        AssignmentRuleMember.objects.create(rule=rule, user=self.agent2, weight=1, active=True)

        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("create_ticket"),
            {
                "subject": "Backend ticket",
                "message": "Initial text",
                "status": "open",
                "prioridad": "normal",
            },
        )
        self.assertEqual(response.status_code, 302)
        ticket = Ticket.objects.get(subject="Backend ticket")
        self.assertEqual(ticket.requester, self.customer)
        self.assertEqual(ticket.assignee, self.agent2)
        self.assertIsNotNone(ticket.first_response_due_at)
        self.assertIsNotNone(ticket.resolution_due_at)
        self.assertTrue(TicketEvent.objects.filter(ticket=ticket, field_name="created").exists())
        self.assertTrue(Comment.objects.filter(ticket=ticket, content="Initial text").exists())
        self.assertTrue(Notification.objects.filter(ticket=ticket, user=self.agent2).exists())

    def test_create_ticket_rejects_empty_subject_without_partial_state(self):
        self.client.force_login(self.agent)
        response = self.client.post(reverse("create_ticket"), {"subject": "", "message": "body"})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(Ticket.objects.filter(description="body").exists())

    def test_end_user_cannot_update_existing_ticket(self):
        ticket = self.make_ticket()
        self.client.force_login(self.customer)
        response = self.client.post(
            f"{reverse('create_ticket')}?id={ticket.id}",
            {"subject": "Changed", "message": "Changed"},
        )
        self.assertEqual(response.status_code, 403)
        ticket.refresh_from_db()
        self.assertEqual(ticket.subject, "Ticket")

    def test_add_comment_updates_status_and_completes_first_response_sla(self):
        ticket = self.make_ticket(first_response_due_at=timezone.now() + timedelta(minutes=30))
        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("add_comment", args=[ticket.id]),
            data=json.dumps({
                "content": "Public response",
                "is_public": True,
                "new_status": "resolved",
            }),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        ticket.refresh_from_db()
        self.assertEqual(ticket.status, "resolved")
        self.assertIsNone(ticket.first_response_due_at)
        self.assertTrue(TicketEvent.objects.filter(ticket=ticket, field_name="status").exists())

    def test_end_user_internal_comment_is_forced_public(self):
        ticket = self.make_ticket()
        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("add_comment", args=[ticket.id]),
            data=json.dumps({"content": "Customer comment", "is_public": False}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        comment = Comment.objects.get(ticket=ticket, content="Customer comment")
        self.assertTrue(comment.is_public)

    def test_merge_is_atomic_and_moves_ticket_data(self):
        source = self.make_ticket(subject="source")
        target = self.make_ticket(subject="target")
        Comment.objects.create(ticket=source, user=self.customer, content="Move me")
        TicketEvent.objects.create(
            ticket=source,
            actor=self.customer,
            field_name="status",
            old_value="pending",
            new_value="open",
            created_at=timezone.now(),
        )

        self.client.force_login(self.agent)
        response = self.client.post(reverse("merge_ticket", args=[source.id]), {"target_ticket_id": target.id})
        self.assertEqual(response.status_code, 200)
        source.refresh_from_db()
        self.assertEqual(source.merged_into, target)
        self.assertEqual(source.status, "closed")
        self.assertTrue(Comment.objects.filter(ticket=target, content="Move me").exists())
        self.assertTrue(TicketEvent.objects.filter(ticket=target, field_name="merge").exists())

    def test_bulk_merge_returns_json(self):
        source = self.make_ticket(subject="source")
        target = self.make_ticket(subject="target")
        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("bulk_merge"),
            data=json.dumps({"ids": [source.id], "target_id": target.id}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["merged"], 1)

    def test_email_ingestion_creates_ticket_and_logs_duplicates(self):
        brand = Brand.objects.create(name="Email Brand", support_email="support@example.com")
        message = {
            "id": "msg-1",
            "conversationId": "conv-1",
            "subject": "Need help",
            "from": {"emailAddress": {"address": "mailcustomer@example.com", "name": "Mail Customer"}},
            "body": {"contentType": "html", "content": "<body><p>Hello</p></body>"},
        }

        self.assertEqual(EmailIngestionService.process_message(message, brand), "ticket_created")
        self.assertEqual(EmailIngestionService.process_message(message, brand), "skip_dup")
        ticket = Ticket.objects.get(email_message_id="msg-1")
        self.assertEqual(ticket.channel, "email")
        log = InboundEmailLog.objects.get(message_id="msg-1", brand=brand)
        self.assertEqual(log.status, InboundEmailLog.STATUS_DUPLICATE)

    def test_email_reply_adds_comment_to_existing_ticket(self):
        brand = Brand.objects.create(name="Reply Brand", support_email="reply@example.com")
        ticket = self.make_ticket(brand=brand, email_conversation_id="conv-reply")
        message = {
            "id": "msg-reply",
            "conversationId": "conv-reply",
            "subject": "Re: Ticket",
            "from": {"emailAddress": {"address": self.customer.email, "name": self.customer.name}},
            "body": {"contentType": "text", "content": "Reply text"},
        }

        self.assertEqual(EmailIngestionService.process_message(message, brand), "comment_added")
        self.assertTrue(Comment.objects.filter(ticket=ticket, email_message_id="msg-reply").exists())

    def test_sla_breach_marks_ticket_and_records_operations(self):
        ticket = self.make_ticket(first_response_due_at=timezone.now() - timedelta(minutes=5))
        breached = SLAService.mark_breaches()
        self.assertEqual(breached, [ticket.id])
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.sla_breached_at)
        self.assertTrue(TicketEvent.objects.filter(ticket=ticket, field_name="sla_breach").exists())
        self.assertTrue(OperationalMetric.objects.filter(name="sla.breach").exists())

    def test_automation_rule_applies_once_per_event(self):
        TicketTag.objects.create(name="escalated")
        AutomationRule.objects.create(
            name="Escalate opens",
            conditions={"status": "open"},
            actions={"priority": "high", "add_tags": ["escalated"]},
        )
        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("create_ticket"),
            {
                "subject": "Automation ticket",
                "message": "Initial text",
                "status": "open",
                "solicitante": str(self.customer.id),
            },
        )
        self.assertEqual(response.status_code, 302)
        ticket = Ticket.objects.get(subject="Automation ticket")
        self.assertEqual(ticket.priority, "high")
        self.assertTrue(ticket.tags.filter(name="escalated").exists())

    def test_assignment_rule_replaces_hardcoded_assignees(self):
        rule = AssignmentRule.objects.create(name="Default")
        AssignmentRuleMember.objects.create(rule=rule, user=self.agent2, weight=1, active=True)

        self.client.force_login(self.customer)
        response = self.client.post(
            reverse("create_ticket"),
            {
                "subject": "Assigned by rule",
                "message": "Body",
                "status": "open",
            },
        )
        self.assertEqual(response.status_code, 302)
        ticket = Ticket.objects.get(subject="Assigned by rule")
        self.assertEqual(ticket.assignee, self.agent2)

    def test_first_response_sla_is_not_rearmed_after_being_answered(self):
        from app.services.comments import CommentService
        from app.services.tickets import TicketService

        SLAPolicy.objects.create(
            name="Default SLA",
            active=True,
            first_response_minutes=60,
            resolution_minutes=240,
        )

        ticket = TicketService.create_ticket(
            self.agent,
            {
                "subject": "SLA ticket",
                "description": "d",
                "content": "",
                "status": "open",
                "requester_id": self.customer.id,
            },
        )
        self.assertIsNotNone(ticket.first_response_due_at)
        self.assertIsNone(ticket.first_responded_at)

        CommentService.add_comment(ticket, self.agent, "respuesta", is_public=True)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.first_response_due_at)
        self.assertIsNotNone(ticket.first_responded_at)

        # Una edicion posterior NO debe re-armar el vencimiento ya cumplido.
        TicketService.update_ticket(
            self.agent,
            ticket.id,
            {"subject": "SLA ticket edit", "description": "d", "status": "open", "priority": "high"},
        )
        ticket.refresh_from_db()
        self.assertIsNone(ticket.first_response_due_at)
        self.assertIsNotNone(ticket.first_responded_at)

    def test_email_html_style_attribute_is_css_sanitized(self):
        from app.sanitizers import sanitize_email_html

        cleaned = sanitize_email_html('<p style="color: red; position: fixed;">hi</p>')
        self.assertIn("color", cleaned)
        # 'position' no esta en la allow-list de propiedades CSS -> se elimina.
        self.assertNotIn("position", cleaned)
