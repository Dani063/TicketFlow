import json
from datetime import timedelta
from unittest.mock import patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from app.models import (
    AssignmentRule,
    AssignmentRuleMember,
    AutomationRule,
    Brand,
    Comment,
    Group,
    InboundEmailLog,
    Notification,
    OperationalMetric,
    OutboundEmailLog,
    ResponseTemplate,
    Role,
    SatisfactionRating,
    SatisfactionReason,
    SLAPolicy,
    Ticket,
    TicketAIAnalysis,
    TicketEvent,
    TicketTag,
    User,
)
from app.permissions import is_agent
from app.services.email_ingestion import EmailIngestionService
from app.services.satisfaction import SatisfactionService
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

    def test_add_comment_empty_error_names_the_missing_message(self):
        ticket = self.make_ticket()
        self.client.force_login(self.agent)
        response = self.client.post(
            reverse("add_comment", args=[ticket.id]),
            data=json.dumps({"content": "   ", "is_public": True}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], {
            "code": "empty_comment",
            "message": "El mensaje no puede estar vacío.",
        })

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

    def test_admin_panel_exposes_assignment_tab_with_agents_only(self):
        """La pestana «Asignacion» solo debe ofrecer agentes, no end users."""
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_panel"))
        self.assertEqual(response.status_code, 200)

        agents = list(response.context["agents"])
        self.assertIn(self.agent, agents)
        self.assertIn(self.agent2, agents)
        self.assertNotIn(self.customer, agents)

        html = response.content.decode()
        self.assertIn('data-tab="assignment"', html)
        self.assertIn('id="ruleMembers"', html)

    def test_legacy_settings_url_redirects_without_restoring_the_removed_screen(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("legacy_settings_redirect"))
        self.assertRedirects(response, reverse("admin_panel"), fetch_redirect_response=False)

        self.client.force_login(self.agent)
        response = self.client.get(reverse("legacy_settings_redirect"))
        self.assertRedirects(response, reverse("profile"), fetch_redirect_response=False)

    def test_admin_panel_scope_options_include_legacy_values(self):
        """Un servicio heredado de Zendesk debe poder elegirse aunque no este en la taxonomia."""
        self.make_ticket(service="servicio_viejo_zendesk", channel="email")

        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_panel"))

        services = {opt["value"]: opt["label"] for opt in response.context["services"]}
        self.assertEqual(services["recordia"], "Recordia")
        self.assertIn("servicio_viejo_zendesk", services)

        channels = {opt["value"]: opt["label"] for opt in response.context["channels"]}
        self.assertEqual(channels["phone"], "Teléfono")
        # Un canal ya presente en la taxonomia no debe duplicarse.
        self.assertEqual(len([o for o in response.context["channels"] if o["value"] == "email"]), 1)

    def test_assignment_rules_api_lists_rule_scope_and_members(self):
        group = Group.objects.create(group_name="Soporte N1")
        rule = AssignmentRule.objects.create(
            name="Solo N1",
            services=["recordia", "ecomfax"],
            channels=["email"],
        )
        rule.groups.add(group)
        AssignmentRuleMember.objects.create(rule=rule, user=self.agent, weight=2, capacity=40, active=True)
        AssignmentRuleMember.objects.create(rule=rule, user=self.agent2, weight=1, active=False)

        self.client.force_login(self.admin)
        response = self.client.get("/api/admin/assignment-rules/")
        self.assertEqual(response.status_code, 200)
        payload = response.json()["rules"]
        data = next(item for item in payload if item["id"] == rule.id)

        self.assertEqual(data["group_names"], ["Soporte N1"])
        self.assertEqual(data["group_ids"], [group.id])
        self.assertEqual(data["services"], ["recordia", "ecomfax"])
        self.assertEqual(data["channels"], ["email"])
        members = {member["user_name"]: member for member in data["members"]}
        self.assertEqual(members["Agent"]["weight"], 2)
        self.assertEqual(members["Agent"]["capacity"], 40)
        self.assertTrue(members["Agent"]["active"])
        self.assertFalse(members["Agent Two"]["active"])

    def test_assignment_rule_scope_matches_any_value_of_each_dimension(self):
        """Ambito multivalor: OR dentro de cada dimension, AND entre dimensiones."""
        from app.services.assignment import AssignmentService

        rule = AssignmentRule.objects.create(
            name="Fax y grabacion por email",
            services=["ecomfax", "recordia"],
            channels=["email", "web"],
        )
        AssignmentRuleMember.objects.create(rule=rule, user=self.agent2, weight=1, active=True)

        # Coincide con el segundo valor de cada lista.
        self.assertEqual(
            AssignmentService.choose_assignee(service="recordia", channel="web"),
            self.agent2,
        )
        # Servicio dentro de la lista pero canal fuera: la regla no aplica.
        self.assertNotIn(
            rule,
            AssignmentService.matching_rules(service="recordia", channel="phone"),
        )
        # Una dimension vacia no restringe.
        rule.channels = []
        rule.save(update_fields=["channels"])
        self.assertIn(rule, AssignmentService.matching_rules(service="ecomfax", channel="phone"))

    def test_assignment_rules_api_saves_multiple_scope_values(self):
        group_a = Group.objects.create(group_name="N1")
        group_b = Group.objects.create(group_name="N2")

        self.client.force_login(self.admin)
        response = self.client.post(
            "/api/admin/assignment-rules/",
            data={
                "name": "Multiambito",
                "active": True,
                "group_ids": [group_a.id, group_b.id],
                "services": ["recordia", "recordia", "", "otros"],
                "channels": ["email"],
                "members": [{"user_id": self.agent.id, "weight": 1}],
            },
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)

        rule = AssignmentRule.objects.get(name="Multiambito")
        self.assertEqual(sorted(rule.groups.values_list("id", flat=True)), sorted([group_a.id, group_b.id]))
        # Duplicados y vacios se descartan al normalizar.
        self.assertEqual(rule.services, ["recordia", "otros"])
        self.assertEqual(rule.channels, ["email"])

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


class _BaseFixture(TestCase):
    """Fixture compartido por las suites del sprint (entidades/SLA/email/IA/reporting)."""

    def setUp(self):
        self.end_role = Role.objects.create(role_name="End user")
        self.agent_role = Role.objects.create(role_name="agent")
        self.admin_role = Role.objects.create(role_name="admin")

        self.customer = User.objects.create_user("customer@example.com", "Customer", "secret")
        self.customer.role = self.end_role
        self.customer.save(update_fields=["role"])

        self.agent = User.objects.create_user("agent@example.com", "Agent", "secret")
        self.agent.role = self.agent_role
        self.agent.save(update_fields=["role"])

        self.admin = User.objects.create_user("admin@example.com", "Admin", "secret")
        self.admin.role = self.admin_role
        self.admin.save(update_fields=["role"])

        self.brand_a = Brand.objects.create(
            name="Comuny", support_email="helpdesk@comuny.example", mailbox_type="m365", language="es"
        )
        self.brand_b = Brand.objects.create(
            name="Ecomfax", support_email="helpdesk@ecomfax.example", mailbox_type="ses", language="es"
        )

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


class EntityTaxonomyTests(_BaseFixture):
    def test_legacy_spanish_type_and_channel_are_normalized_on_create(self):
        from app.services.tickets import TicketService

        ticket = TicketService.create_or_update_from_post(
            self.agent,
            {"subject": "Legacy", "message": "b", "tipo": "incidencia", "canal": "telefono"},
        )
        self.assertEqual(ticket.type, "incident")
        self.assertEqual(ticket.channel, "phone")

    def test_web_ticket_defaults_channel_web_only_on_create(self):
        from app.services.tickets import TicketService

        ticket = TicketService.create_or_update_from_post(
            self.agent, {"subject": "Web ticket", "message": "b"}
        )
        self.assertEqual(ticket.channel, "web")

        # En update, canal vacío NO machaca con 'web': queda None (comportamiento previo)
        email_ticket = self.make_ticket(channel="email")
        updated = TicketService.create_or_update_from_post(
            self.agent, {"subject": "Edited", "message": ""}, ticket_id=email_ticket.id
        )
        self.assertIsNone(updated.channel)

    def test_unknown_brand_is_rejected_instead_of_creating_phantom(self):
        from app.api import APIValidationError
        from app.services.tickets import TicketService

        with self.assertRaises(APIValidationError) as ctx:
            TicketService.create_or_update_from_post(
                self.agent, {"subject": "X", "message": "b", "empresa": "NoExiste S.L."}
            )
        self.assertEqual(ctx.exception.code, "brand_not_found")
        self.assertFalse(Brand.objects.filter(name="NoExiste S.L.").exists())

    def test_incident_links_to_problem_and_invalid_targets_are_rejected(self):
        from app.api import APIValidationError
        from app.services.tickets import TicketService

        problem = self.make_ticket(subject="Problema raiz", type="problem")
        question = self.make_ticket(subject="Consulta", type="question")

        incident = TicketService.create_or_update_from_post(
            self.agent,
            {"subject": "Incidente", "message": "b", "tipo": "incident", "problem_id": str(problem.id)},
        )
        self.assertEqual(incident.problem_id, problem.id)
        self.assertIn(incident, problem.incidents.all())
        self.assertTrue(
            TicketEvent.objects.filter(ticket=incident, field_name="problem_id").exists()
            or incident.problem_id == problem.id  # el evento se emite en update; en create basta el vínculo
        )

        # Vincular a un ticket que no es problema -> 400
        with self.assertRaises(APIValidationError):
            TicketService.create_or_update_from_post(
                self.agent,
                {"subject": "Mal vinculo", "message": "b", "tipo": "incident", "problem_id": str(question.id)},
            )

        # Solo los incidentes pueden vincularse
        with self.assertRaises(APIValidationError):
            TicketService.create_or_update_from_post(
                self.agent,
                {"subject": "Tarea", "message": "b", "tipo": "task", "problem_id": str(problem.id)},
            )

    def test_ticket_detail_api_exposes_problem_link_and_incidents(self):
        problem = self.make_ticket(subject="Problema", type="problem")
        incident = self.make_ticket(subject="Incidente", type="incident", problem=problem)

        self.client.force_login(self.agent)
        detail = self.client.get(reverse("ticket_detail_api", args=[incident.id])).json()
        self.assertEqual(detail["problem_id"], problem.id)
        self.assertEqual(detail["problem_subject"], "Problema")

        detail = self.client.get(reverse("ticket_detail_api", args=[problem.id])).json()
        self.assertEqual([i["id"] for i in detail["incidents"]], [incident.id])

    def test_filter_tickets_brand_param_scopes_rows_and_counts(self):
        self.make_ticket(subject="A1", brand=self.brand_a)
        self.make_ticket(subject="A2", brand=self.brand_a)
        self.make_ticket(subject="B1", brand=self.brand_b)

        self.client.force_login(self.agent)
        response = self.client.get(
            f"{reverse('filter_tickets')}?view=mis_tickets&brand_id={self.brand_a.id}"
            "&counts=1&force_counts=1"
        )
        data = response.json()
        subjects = {t["subject"] for t in data["tickets"]}
        self.assertEqual(subjects, {"A1", "A2"})
        self.assertEqual(data["filtros"]["mis_tickets"], 2)
        self.assertTrue(all(t["brand"] == "Comuny" for t in data["tickets"]))


class BalancedUnresolvedTicketPaginationTests(_BaseFixture):
    def _make_agent(self, name, email):
        agent = User.objects.create_user(email, name, "secret")
        agent.role = self.agent_role
        agent.save(update_fields=["role"])
        return agent

    def test_active_assignment_members_receive_equal_per_agent_pages(self):
        agent_b = self._make_agent("Agent B", "agent-b@example.com")
        agent_c = self._make_agent("Agent C", "agent-c@example.com")
        outsider = self._make_agent("Agent Outside", "outside@example.com")
        inactive_member = self._make_agent("Agent Inactive", "inactive@example.com")

        rule = AssignmentRule.objects.create(name="Default inbound", active=True)
        for agent in (self.agent, agent_b, agent_c):
            AssignmentRuleMember.objects.create(rule=rule, user=agent, active=True)
        AssignmentRuleMember.objects.create(rule=rule, user=inactive_member, active=False)

        expected_ids = {self.agent.name: [], agent_b.name: [], agent_c.name: []}
        for agent, amount in ((self.agent, 8), (agent_b, 8), (agent_c, 1)):
            for index in range(amount):
                ticket = self.make_ticket(subject=f"{agent.name} {index}", assignee=agent)
                expected_ids[agent.name].append(ticket.id)

        # Ninguno de estos tickets debe consumir filas del filtro balanceado.
        self.make_ticket(subject="Fuera de regla 1", assignee=outsider)
        self.make_ticket(subject="Fuera de regla 2", assignee=outsider)
        self.make_ticket(subject="Miembro desactivado", assignee=inactive_member)
        self.make_ticket(subject="Sin asignar", assignee=None)
        self.make_ticket(subject="Tarea", assignee=self.agent, type="task")
        self.make_ticket(subject="Resolved", assignee=self.agent, status="resolved")
        self.make_ticket(subject="Closed", assignee=self.agent, status="closed")

        self.client.force_login(self.agent)
        endpoint = reverse("filter_tickets")
        query = "view=all_unsolved_no_tareas&page_size=10&fast=1&sort_by=id&sort_dir=asc"

        first = self.client.get(f"{endpoint}?{query}&page=1").json()
        second = self.client.get(f"{endpoint}?{query}&page=2").json()

        self.assertEqual(first["pagination"]["total"], 17)
        self.assertEqual(first["pagination"]["total_pages"], 2)
        self.assertTrue(first["pagination"]["exact"])
        self.assertEqual(first["group_by"], "assignee")
        self.assertTrue(first["balanced_assignment"]["enabled"])

        first_by_agent = {}
        second_by_agent = {}
        for row in first["tickets"]:
            first_by_agent.setdefault(row["assignee"], []).append(row["id"])
        for row in second["tickets"]:
            second_by_agent.setdefault(row["assignee"], []).append(row["id"])

        # C solo tiene un ticket; el resto de su cupo se redistribuye A/B.
        self.assertEqual({name: len(ids) for name, ids in first_by_agent.items()}, {
            "Agent": 5, "Agent B": 4, "Agent C": 1,
        })
        self.assertEqual({name: len(ids) for name, ids in second_by_agent.items()}, {
            "Agent": 3, "Agent B": 4,
        })
        self.assertEqual(first_by_agent["Agent"], expected_ids["Agent"][:5])
        self.assertEqual(second_by_agent["Agent"], expected_ids["Agent"][5:])
        self.assertEqual(first_by_agent["Agent B"], expected_ids["Agent B"][:4])
        self.assertEqual(second_by_agent["Agent B"], expected_ids["Agent B"][4:])

        exact_total = self.client.get(
            f"{endpoint}?view=all_unsolved_no_tareas&page_size=10&total_only=1"
        ).json()
        self.assertEqual(exact_total["pagination"]["total"], 17)

        counts = self.client.get(
            f"{endpoint}?counts=1&counts_only=1&force_counts=1&page_size=10"
        ).json()["filtros"]
        self.assertEqual(counts["all_unsolved_no_tareas"], 17)

        # El cambio está encapsulado: la otra vista no resuelta sigue usando su
        # conjunto global y conserva los tickets fuera de las reglas.
        global_unsolved = self.client.get(
            f"{endpoint}?view=unsolved_no_tareas&page_size=10&total_only=1"
        ).json()
        self.assertEqual(global_unsolved["pagination"]["total"], 21)

    def test_more_agents_than_rows_continue_on_the_next_page(self):
        rule = AssignmentRule.objects.create(name="Many agents", active=True)
        agents = []
        for index in range(12):
            agent = self._make_agent(f"Pool {index:02d}", f"pool-{index:02d}@example.com")
            agents.append(agent)
            AssignmentRuleMember.objects.create(rule=rule, user=agent, active=True)
            self.make_ticket(subject=f"Pool ticket {index:02d}", assignee=agent)

        self.client.force_login(self.agent)
        endpoint = reverse("filter_tickets")
        query = "view=all_unsolved_no_tareas&page_size=10&fast=1&sort_by=id&sort_dir=asc"
        first = self.client.get(f"{endpoint}?{query}&page=1").json()
        second = self.client.get(f"{endpoint}?{query}&page=2").json()

        self.assertEqual([row["assignee"] for row in first["tickets"]], [a.name for a in agents[:10]])
        self.assertEqual([row["assignee"] for row in second["tickets"]], [a.name for a in agents[10:]])
        self.assertEqual(second["pagination"]["page"], 2)
        self.assertEqual(second["pagination"]["total"], 12)


class SLAEnhancementTests(_BaseFixture):
    def test_brand_specific_policy_wins_over_generic(self):
        SLAPolicy.objects.create(name="Generica", first_response_minutes=60, resolution_minutes=240)
        SLAPolicy.objects.create(
            name="Comuny VIP", brand=self.brand_a, first_response_minutes=15, resolution_minutes=60
        )
        ticket = self.make_ticket(brand=self.brand_a, first_response_due_at=None)
        policy = SLAService.apply_policy(ticket)
        self.assertEqual(policy.name, "Comuny VIP")
        self.assertAlmostEqual(
            (ticket.first_response_due_at - ticket.created_at).total_seconds() / 60, 15, delta=1
        )

    def test_first_response_met_is_persisted_at_response_time(self):
        on_time = self.make_ticket(first_response_due_at=timezone.now() + timedelta(minutes=30))
        SLAService.record_first_response(on_time, self.agent)
        on_time.refresh_from_db()
        self.assertIs(on_time.first_response_met, True)
        self.assertIsNone(on_time.first_response_due_at)

        late = self.make_ticket(first_response_due_at=timezone.now() - timedelta(minutes=5))
        SLAService.record_first_response(late, self.agent)
        late.refresh_from_db()
        self.assertIs(late.first_response_met, False)

    def test_breach_escalates_priority_and_notifies_group(self):
        from app.models import Group

        group = Group.objects.create(group_name="Soporte N1")
        member = User.objects.create_user("n1@example.com", "N1", "secret")
        member.role = self.agent_role
        member.group = group
        member.save(update_fields=["role", "group"])

        ticket = self.make_ticket(
            priority="normal",
            assigned_group=group,
            first_response_due_at=timezone.now() - timedelta(minutes=10),
        )
        breached = SLAService.mark_breaches()
        self.assertEqual(breached, [ticket.id])
        ticket.refresh_from_db()
        self.assertEqual(ticket.priority, "high")
        self.assertIsNotNone(ticket.sla_escalated_at)
        self.assertTrue(
            TicketEvent.objects.filter(ticket=ticket, field_name="priority", new_value="high").exists()
        )
        self.assertTrue(Notification.objects.filter(ticket=ticket, user=member).exists())

    def test_notify_at_risk_warns_assignee_once(self):
        ticket = self.make_ticket(first_response_due_at=timezone.now() + timedelta(minutes=30))
        first = SLAService.notify_at_risk(window_minutes=60)
        self.assertEqual(first, [ticket.id])
        self.assertTrue(Notification.objects.filter(ticket=ticket, user=self.agent).exists())
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.sla_risk_notified_at)

        # Idempotente: segunda pasada no repite el aviso
        self.assertEqual(SLAService.notify_at_risk(window_minutes=60), [])

    def test_sla_sort_orders_by_next_due_with_nulls_last(self):
        soon = self.make_ticket(subject="Vence pronto", first_response_due_at=timezone.now() + timedelta(minutes=10))
        later = self.make_ticket(subject="Vence tarde", resolution_due_at=timezone.now() + timedelta(hours=5))
        no_sla = self.make_ticket(subject="Sin SLA")

        self.client.force_login(self.agent)
        data = self.client.get(
            f"{reverse('filter_tickets')}?view=mis_tickets&sort_by=sla&sort_dir=asc"
        ).json()
        ids = [t["id"] for t in data["tickets"]]
        self.assertEqual(ids, [soon.id, later.id, no_sla.id])
        self.assertTrue(data["tickets"][0]["sla"]["at_risk"])

    def test_sla_views_and_counters(self):
        breached = self.make_ticket(subject="Breached", sla_breached_at=timezone.now())
        at_risk = self.make_ticket(
            subject="At risk", first_response_due_at=timezone.now() + timedelta(minutes=30)
        )
        self.make_ticket(subject="Tranquilo")

        self.client.force_login(self.agent)
        data = self.client.get(f"{reverse('filter_tickets')}?view=sla_breached").json()
        self.assertEqual([t["id"] for t in data["tickets"]], [breached.id])

        data = self.client.get(f"{reverse('filter_tickets')}?view=sla_at_risk").json()
        self.assertEqual([t["id"] for t in data["tickets"]], [at_risk.id])

        counts = self.client.get(
            f"{reverse('filter_tickets')}?counts=1&counts_only=1&force_counts=1"
        ).json()["filtros"]
        self.assertEqual(counts["sla_breached"], 1)
        self.assertEqual(counts["sla_at_risk"], 1)


class ResponseTemplateTests(_BaseFixture):
    def test_seed_templates_exist_with_threading_token(self):
        for lang in ("es", "en"):
            tpl = ResponseTemplate.objects.get(key="ticket_created", brand__isnull=True, language=lang)
            self.assertIn("[Ticket #{{ticket_id}}]", tpl.subject)

    def test_language_pick_normalizes_zendesk_values(self):
        from app.services.email_templates import ResponseTemplateService

        self.assertEqual(ResponseTemplateService.pick_language(self.make_ticket(language="inglés")), "en")
        self.assertEqual(ResponseTemplateService.pick_language(self.make_ticket(language="español")), "es")
        # Sin idioma de ticket: hereda el de la marca; sin nada: es
        self.brand_a.language = "en"
        self.brand_a.save(update_fields=["language"])
        self.assertEqual(
            ResponseTemplateService.pick_language(self.make_ticket(brand=self.brand_a, language=None)), "en"
        )
        self.assertEqual(ResponseTemplateService.pick_language(self.make_ticket(language=None)), "es")

    def test_resolution_cascade_prefers_brand_then_language(self):
        from app.services.email_templates import ResponseTemplateService

        brand_tpl = ResponseTemplate.objects.create(
            key="ticket_created", brand=self.brand_a, language="es",
            subject="[Ticket #{{ticket_id}}] Marca", body_html="<p>marca</p>",
        )
        self.assertEqual(
            ResponseTemplateService.resolve("ticket_created", brand=self.brand_a, language="es").id,
            brand_tpl.id,
        )
        # Idioma sin plantilla de marca -> global del idioma
        resolved = ResponseTemplateService.resolve("ticket_created", brand=self.brand_a, language="en")
        self.assertIsNone(resolved.brand_id)
        self.assertEqual(resolved.language, "en")

    def test_render_substitutes_scalar_variables(self):
        from app.services.email_templates import ResponseTemplateService

        ticket = self.make_ticket(subject="No funciona", brand=self.brand_a)
        tpl = ResponseTemplate.objects.get(key="ticket_created", brand__isnull=True, language="es")
        rendered = ResponseTemplateService.render(tpl, ResponseTemplateService.context_for_ticket(ticket))
        self.assertIn(f"[Ticket #{ticket.id}]", rendered["subject"])
        self.assertIn("No funciona", rendered["subject"])
        self.assertIn("Customer", rendered["body_html"])
        self.assertIn("Comuny", rendered["body_html"])

    def test_admin_templates_api_requires_admin_and_does_crud(self):
        url = reverse("admin_response_templates_api")
        self.assertEqual(self.client.get(url).status_code, 401)
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get(url).status_code, 403)

        self.client.force_login(self.admin)
        created = self.client.post(
            url,
            data=json.dumps({
                "key": "ticket_created", "brand_id": self.brand_a.id, "language": "es",
                "subject": "[Ticket #{{ticket_id}}] Hola", "body_html": "<p>Hola {{requester_name}}</p>",
            }),
            content_type="application/json",
        )
        self.assertEqual(created.status_code, 200)
        tpl_id = created.json()["template"]["id"]

        # Duplicado (key, marca, idioma) -> 400
        dup = self.client.post(
            url,
            data=json.dumps({
                "key": "ticket_created", "brand_id": self.brand_a.id, "language": "es",
                "subject": "x", "body_html": "<p>x</p>",
            }),
            content_type="application/json",
        )
        self.assertEqual(dup.status_code, 400)

        # body_html se sanea (script fuera)
        updated = self.client.put(
            url,
            data=json.dumps({
                "id": tpl_id, "key": "ticket_created", "brand_id": self.brand_a.id, "language": "es",
                "subject": "[Ticket #{{ticket_id}}] Hola", "body_html": "<p>ok</p><script>alert(1)</script>",
            }),
            content_type="application/json",
        )
        self.assertEqual(updated.status_code, 200)
        self.assertNotIn("<script>", updated.json()["template"]["body_html"])

        # Preview renderiza con datos de ejemplo
        preview = self.client.post(
            f"{url}?action=preview",
            data=json.dumps({"subject": "[Ticket #{{ticket_id}}]", "body_html": "<p>{{requester_name}}</p>"}),
            content_type="application/json",
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn("12345", preview.json()["preview"]["subject"])

        # DELETE desactiva (no borra)
        deleted = self.client.delete(f"{url}?id={tpl_id}")
        self.assertEqual(deleted.status_code, 200)
        self.assertFalse(ResponseTemplate.objects.get(id=tpl_id).active)


@override_settings(OUTBOUND_EMAIL_ENABLED=True)
class OutboundEmailTests(_BaseFixture):
    def _create_ticket_via_service(self, **extra):
        from app.services.tickets import TicketService

        payload = {
            "subject": "Necesito ayuda",
            "description": "b",
            "content": "",
            "status": "open",
            "requester_id": self.customer.id,
            "brand": self.brand_a,
            "channel": "web",
        }
        payload.update(extra)
        return TicketService.create_ticket(self.agent, payload)

    def test_confirmation_is_queued_with_threading_token(self):
        ticket = self._create_ticket_via_service()
        log = OutboundEmailLog.objects.get(ticket=ticket)
        self.assertEqual(log.status, OutboundEmailLog.STATUS_QUEUED)
        self.assertEqual(log.to_email, self.customer.email)
        self.assertEqual(log.provider, "m365")
        self.assertIn(f"[Ticket #{ticket.id}]", log.subject)
        self.assertIn("Customer", log.payload["body_html"])

    def test_confirmation_is_deduplicated_per_ticket_and_recipient(self):
        from app.services.email_outbound import OutboundEmailService

        ticket = self._create_ticket_via_service()
        OutboundEmailService.queue_ticket_confirmation(ticket)  # segunda llamada (redelivery)
        self.assertEqual(OutboundEmailLog.objects.filter(ticket=ticket).count(), 1)

    @override_settings(OUTBOUND_EMAIL_ENABLED=False)
    def test_kill_switch_suppresses_but_logs(self):
        ticket = self._create_ticket_via_service()
        log = OutboundEmailLog.objects.get(ticket=ticket)
        self.assertEqual(log.status, OutboundEmailLog.STATUS_SUPPRESSED)
        self.assertEqual(log.result, "disabled")

    def test_own_mailbox_and_noreply_recipients_are_suppressed(self):
        own = User.objects.create_user("helpdesk@ecomfax.example", "Other Brand Mailbox", "secret")
        ticket = self._create_ticket_via_service(requester_id=own.id)
        self.assertEqual(OutboundEmailLog.objects.get(ticket=ticket).result, "own_mailbox")

        noreply = User.objects.create_user("no-reply@cliente.example", "NoReply", "secret")
        ticket2 = self._create_ticket_via_service(requester_id=noreply.id)
        self.assertEqual(OutboundEmailLog.objects.get(ticket=ticket2).result, "noreply_pattern")

    def test_auto_generated_inbound_email_never_gets_confirmation(self):
        message = {
            "id": "auto-1",
            "conversationId": "conv-auto",
            "subject": "Out of office",
            "from": {"emailAddress": {"address": "alguien@cliente.example", "name": "Alguien"}},
            "body": {"contentType": "html", "content": "<body><p>OOO</p></body>"},
            "internetMessageHeaders": [{"name": "Auto-Submitted", "value": "auto-replied"}],
        }
        self.assertEqual(EmailIngestionService.process_message(message, self.brand_a), "ticket_created")
        ticket = Ticket.objects.get(email_message_id="auto-1")
        self.assertFalse(OutboundEmailLog.objects.filter(ticket=ticket).exists())

    def test_normal_inbound_email_queues_confirmation(self):
        message = {
            "id": "normal-1",
            "conversationId": "conv-normal",
            "subject": "Ayuda",
            "from": {"emailAddress": {"address": "cliente@cliente.example", "name": "Cliente"}},
            "body": {"contentType": "html", "content": "<body><p>Hola</p></body>"},
        }
        EmailIngestionService.process_message(message, self.brand_a)
        ticket = Ticket.objects.get(email_message_id="normal-1")
        log = OutboundEmailLog.objects.get(ticket=ticket)
        self.assertEqual(log.status, OutboundEmailLog.STATUS_QUEUED)

    def test_deliver_routes_by_mailbox_type(self):
        from app.services.email_outbound import OutboundEmailService

        ses_ticket = self.make_ticket(brand=self.brand_b)
        ses_log = OutboundEmailLog.objects.create(
            brand=self.brand_b, ticket=ses_ticket, provider="ses",
            to_email="c@x.example", subject="s", payload={"body_html": "<p>h</p>", "body_text": "h"},
        )
        with patch("boto3.client") as mock_boto:
            mock_boto.return_value.send_email.return_value = {"MessageId": "ses-123"}
            message_id = OutboundEmailService.deliver(ses_log)
        self.assertEqual(message_id, "ses-123")
        kwargs = mock_boto.return_value.send_email.call_args.kwargs
        self.assertIn("Ecomfax", kwargs["FromEmailAddress"])
        headers = {h["Name"]: h["Value"] for h in kwargs["Content"]["Simple"]["Headers"]}
        self.assertEqual(headers["Auto-Submitted"], "auto-generated")

        m365_ticket = self.make_ticket(brand=self.brand_a)
        m365_log = OutboundEmailLog.objects.create(
            brand=self.brand_a, ticket=m365_ticket, provider="m365",
            to_email="c@x.example", subject="s", payload={"body_html": "<p>h</p>"},
        )
        with patch("app.services.msgraph.get_graph_token", return_value="tok"), \
             patch("requests.post") as mock_post:
            mock_post.return_value.status_code = 202
            mock_post.return_value.raise_for_status.return_value = None
            message_id = OutboundEmailService.deliver(m365_log)
        self.assertIsNone(message_id)
        called_url = mock_post.call_args.args[0]
        self.assertIn("helpdesk@comuny.example/sendMail", called_url)
        sent_json = mock_post.call_args.kwargs["json"]
        self.assertEqual(sent_json["message"]["toRecipients"][0]["emailAddress"]["address"], "c@x.example")

    def test_send_task_marks_sent_and_is_idempotent(self):
        from app.tasks import send_outbound_email

        ticket = self.make_ticket(brand=self.brand_b)
        log = OutboundEmailLog.objects.create(
            brand=self.brand_b, ticket=ticket, provider="ses",
            to_email="c@x.example", subject="s", payload={"body_html": "<p>h</p>"},
        )
        with patch("app.services.email_outbound.OutboundEmailService.deliver", return_value="mid-1") as mock_deliver:
            result = send_outbound_email.apply(args=(log.id,)).result
        self.assertEqual(result, "sent")
        log.refresh_from_db()
        self.assertEqual(log.status, OutboundEmailLog.STATUS_SENT)
        self.assertEqual(log.provider_message_id, "mid-1")
        self.assertEqual(log.attempts, 1)

        # Redelivery de SQS: no se reenvía
        with patch("app.services.email_outbound.OutboundEmailService.deliver") as mock_deliver:
            result = send_outbound_email.apply(args=(log.id,)).result
        self.assertEqual(result, "already_sent")
        mock_deliver.assert_not_called()

    def test_send_task_marks_failed_after_retries_exhausted(self):
        from app.tasks import send_outbound_email

        ticket = self.make_ticket(brand=self.brand_b)
        log = OutboundEmailLog.objects.create(
            brand=self.brand_b, ticket=ticket, provider="ses",
            to_email="c@x.example", subject="s", payload={},
        )
        with patch(
            "app.services.email_outbound.OutboundEmailService.deliver",
            side_effect=RuntimeError("SES sandbox"),
        ):
            result = send_outbound_email.apply(args=(log.id,), retries=5).result
        self.assertEqual(result, "failed")
        log.refresh_from_db()
        self.assertEqual(log.status, OutboundEmailLog.STATUS_FAILED)
        self.assertIn("SES sandbox", log.error)


AI_FIXTURE = {
    "ticket_type": "incident",
    "category": "app_functionality",
    "priority": "high",
    "language": "es",
    "confidence": {"ticket_type": 0.92, "category": 0.81, "priority": 0.88, "language": 0.99},
    "reasoning": "El servicio dejó de funcionar de forma no planificada.",
}
AI_META = {"model": "gpt-4o-mini", "input_tokens": 500, "output_tokens": 60, "latency_ms": 800}


@override_settings(AI_CLASSIFICATION_ENABLED=True, AI_AUTO_APPLY=True, AI_AUTO_APPLY_CONFIDENCE=0.7)
class TicketAITests(_BaseFixture):
    def _empty_ticket(self, **overrides):
        values = {"priority": None, "type": None, "category": None, "language": None}
        values.update(overrides)
        return self.make_ticket(**values)

    def test_happy_path_applies_fields_with_audit(self):
        from app.services.ticket_ai import TicketAIService

        ticket = self._empty_ticket(subject="El portal da error 500", brand=self.brand_a)
        with patch("app.services.ticket_ai.ai.chat_json", return_value=(AI_FIXTURE, AI_META)):
            result = TicketAIService.classify_and_apply(ticket)

        ticket.refresh_from_db()
        self.assertEqual(ticket.type, "incident")
        self.assertEqual(ticket.category, "app_functionality")
        self.assertEqual(ticket.priority, "high")
        self.assertEqual(ticket.language, "es")
        self.assertEqual(set(result.replace("applied:", "").split(",")),
                         {"type", "category", "priority", "language"})

        analysis = TicketAIAnalysis.objects.get(ticket=ticket)
        self.assertEqual(analysis.status, TicketAIAnalysis.STATUS_SUCCESS)
        self.assertEqual(analysis.model_name, "gpt-4o-mini")
        self.assertEqual(sorted(analysis.applied_fields),
                         ["category", "language", "priority", "type"])
        ai_events = TicketEvent.objects.filter(
            ticket=ticket, actor__email="ai-assistant@ticketflow.local"
        )
        self.assertEqual(ai_events.count(), 4)

    def test_human_values_are_never_overwritten(self):
        from app.services.ticket_ai import TicketAIService

        ticket = self._empty_ticket(type="question", priority="low")
        with patch("app.services.ticket_ai.ai.chat_json", return_value=(AI_FIXTURE, AI_META)):
            TicketAIService.classify_and_apply(ticket)
        ticket.refresh_from_db()
        self.assertEqual(ticket.type, "question")
        self.assertEqual(ticket.priority, "low")
        analysis = TicketAIAnalysis.objects.get(ticket=ticket)
        self.assertEqual(analysis.suggested_type, "incident")  # la sugerencia queda guardada
        self.assertNotIn("type", analysis.applied_fields)

    def test_low_confidence_fields_are_suggestion_only(self):
        from app.services.ticket_ai import TicketAIService

        fixture = dict(AI_FIXTURE, confidence={"ticket_type": 0.4, "category": 0.4, "priority": 0.4, "language": 0.4})
        ticket = self._empty_ticket()
        with patch("app.services.ticket_ai.ai.chat_json", return_value=(fixture, AI_META)):
            TicketAIService.classify_and_apply(ticket)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.type)
        self.assertEqual(TicketAIAnalysis.objects.get(ticket=ticket).applied_fields, [])

    @override_settings(AI_AUTO_APPLY=False)
    def test_auto_apply_flag_disables_writes(self):
        from app.services.ticket_ai import TicketAIService

        ticket = self._empty_ticket()
        with patch("app.services.ticket_ai.ai.chat_json", return_value=(AI_FIXTURE, AI_META)):
            TicketAIService.classify_and_apply(ticket)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.type)
        self.assertEqual(TicketAIAnalysis.objects.filter(ticket=ticket).count(), 1)

    def test_out_of_enum_values_are_dropped(self):
        from app.services.ticket_ai import TicketAIService

        fixture = dict(AI_FIXTURE, ticket_type="bug", priority="critical")
        ticket = self._empty_ticket()
        with patch("app.services.ticket_ai.ai.chat_json", return_value=(fixture, AI_META)):
            TicketAIService.classify_and_apply(ticket)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.type)       # 'bug' no es un tipo valido
        self.assertIsNone(ticket.priority)   # 'critical' tampoco
        self.assertEqual(ticket.category, "app_functionality")  # el resto sí aplica

    def test_idempotent_by_fingerprint_unless_forced(self):
        from app.services.ticket_ai import TicketAIService

        ticket = self._empty_ticket()
        with patch("app.services.ticket_ai.ai.chat_json", return_value=(AI_FIXTURE, AI_META)):
            TicketAIService.classify_and_apply(ticket)
            self.assertEqual(TicketAIService.classify_and_apply(ticket), "skip_dup")
            TicketAIService.classify_and_apply(ticket, force=True)
        self.assertEqual(TicketAIAnalysis.objects.filter(ticket=ticket).count(), 2)

    def test_applied_priority_recomputes_sla(self):
        from app.services.ticket_ai import TicketAIService

        SLAPolicy.objects.create(name="High SLA", priority="high", first_response_minutes=60)
        ticket = self._empty_ticket()
        self.assertIsNone(ticket.first_response_due_at)
        with patch("app.services.ticket_ai.ai.chat_json", return_value=(AI_FIXTURE, AI_META)):
            TicketAIService.classify_and_apply(ticket)
        ticket.refresh_from_db()
        self.assertEqual(ticket.priority, "high")
        self.assertIsNotNone(ticket.first_response_due_at)

    def test_permanent_error_marks_failed_without_breaking_ticket(self):
        from app.services.ai import AIPermanentError
        from app.tasks import classify_ticket_ai

        ticket = self._empty_ticket()
        with patch(
            "app.services.ticket_ai.ai.chat_json", side_effect=AIPermanentError("bad deployment")
        ):
            result = classify_ticket_ai.apply(args=(ticket.id,)).result
        self.assertEqual(result, "failed")
        analysis = TicketAIAnalysis.objects.get(ticket=ticket)
        self.assertEqual(analysis.status, TicketAIAnalysis.STATUS_FAILED)
        self.assertIn("bad deployment", analysis.error)
        ticket.refresh_from_db()
        self.assertIsNone(ticket.type)

    def test_dispatch_hook_fires_on_commit_only_when_enabled(self):
        from app.services.tickets import TicketService

        payload = {
            "subject": "Hook",
            "description": "b",
            "content": "",
            "status": "open",
            "requester_id": self.customer.id,
        }
        with patch("app.tasks.classify_ticket_ai") as mock_task:
            with self.captureOnCommitCallbacks(execute=True):
                ticket = TicketService.create_ticket(self.agent, payload)
            mock_task.delay.assert_called_once_with(ticket.id)

        with override_settings(AI_CLASSIFICATION_ENABLED=False):
            with patch("app.tasks.classify_ticket_ai") as mock_task:
                with self.captureOnCommitCallbacks(execute=True):
                    TicketService.create_ticket(self.agent, dict(payload, subject="Hook2"))
                mock_task.delay.assert_not_called()

    def test_long_bodies_are_truncated_for_cost_control(self):
        from app.services.ticket_ai import TicketAIService

        ticket = self._empty_ticket(description="x" * 50000)
        captured = {}

        def fake_chat(messages, **kwargs):
            captured["user"] = messages[1]["content"]
            return AI_FIXTURE, AI_META

        with patch("app.services.ticket_ai.ai.chat_json", side_effect=fake_chat):
            TicketAIService.classify_and_apply(ticket)
        self.assertIn("…[truncado]", captured["user"])
        self.assertLess(len(captured["user"]), 8000)


class AIProviderTests(TestCase):
    """Comportamiento interno de ai.chat_json (truncado y fallback de formato)."""

    @staticmethod
    def _resp(content, finish_reason="stop"):
        from types import SimpleNamespace

        return SimpleNamespace(
            choices=[SimpleNamespace(
                message=SimpleNamespace(content=content),
                finish_reason=finish_reason,
            )],
            usage=SimpleNamespace(prompt_tokens=10, completion_tokens=20),
            model="gpt-4o-mini",
        )

    @staticmethod
    def _client(create):
        from types import SimpleNamespace

        return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    def test_truncated_response_raises_permanent_not_retryable(self):
        from app.services import ai
        from app.services.ai import AIPermanentError

        client = self._client(lambda **kw: self._resp("{\"ticket_type\":\"inci", finish_reason="length"))
        with patch("app.services.ai._get_client", return_value=client):
            with self.assertRaises(AIPermanentError):
                ai.chat_json([{"role": "user", "content": "x"}])

    def test_bad_request_falls_back_to_json_object_regardless_of_message(self):
        import httpx
        import openai

        from app.services import ai

        calls = []

        def create(**kw):
            fmt = kw["response_format"]["type"]
            calls.append(fmt)
            if fmt == "json_schema":
                # Mensaje SIN la substring "response_format" a propósito: el
                # fallback debe dispararse por el tipo BadRequestError, no por texto.
                resp = httpx.Response(400, request=httpx.Request("POST", "http://x"))
                raise openai.BadRequestError("unsupported parameter for this model", response=resp, body=None)
            return self._resp('{"ok": true}')

        client = self._client(create)
        with patch("app.services.ai._get_client", return_value=client):
            parsed, meta = ai.chat_json(
                [{"role": "user", "content": "x"}],
                json_schema={"name": "s", "strict": True, "schema": {}},
            )
        self.assertEqual(parsed, {"ok": True})
        self.assertEqual(calls, ["json_schema", "json_object"])

    def test_bad_request_on_json_object_propagates_as_permanent(self):
        import httpx
        import openai

        from app.services import ai
        from app.services.ai import AIPermanentError

        def create(**kw):
            resp = httpx.Response(400, request=httpx.Request("POST", "http://x"))
            raise openai.BadRequestError("content filtered", response=resp, body=None)

        client = self._client(create)
        with patch("app.services.ai._get_client", return_value=client):
            with self.assertRaises(AIPermanentError):
                ai.chat_json([{"role": "user", "content": "x"}])


class ReportingTests(_BaseFixture):
    def setUp(self):
        super().setUp()
        self.t_a1 = self.make_ticket(subject="RA1", brand=self.brand_a, status="open")
        self.t_a2 = self.make_ticket(
            subject="RA2", brand=self.brand_a, status="closed",
            closed_at=timezone.now(), sla_breached_at=timezone.now(),
        )
        self.t_b = self.make_ticket(subject="RB1", brand=self.brand_b, status="open")
        self.t_none = self.make_ticket(subject="RN1", brand=None, status="pending")

    def test_reporting_data_requires_agent(self):
        url = reverse("reporting_data")
        self.assertEqual(self.client.get(url).status_code, 401)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.get(url).status_code, 403)

    def test_reporting_data_aggregates_and_brand_filter(self):
        self.client.force_login(self.agent)
        data = self.client.get(reverse("reporting_data")).json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["kpis"]["created"], 4)
        self.assertEqual(data["kpis"]["backlog"], 3)  # open/pending vivos
        self.assertEqual(data["kpis"]["sla_breached"], 1)
        brands_in_breakdown = {r["brand"] for r in data["by_brand"]}
        self.assertIn("— Sin marca —", brands_in_breakdown)

        scoped = self.client.get(f"{reverse('reporting_data')}?brand={self.brand_a.id}").json()
        self.assertEqual(scoped["kpis"]["created"], 2)
        self.assertEqual(scoped["kpis"]["sla_breached"], 1)
        self.assertEqual(scoped["kpis"]["backlog"], 1)

    def test_reporting_csv_streams_filtered_rows(self):
        self.client.force_login(self.agent)
        response = self.client.get(f"{reverse('reporting_export_csv')}?brand={self.brand_a.id}")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/csv", response["Content-Type"])
        content = b"".join(response.streaming_content).decode("utf-8")
        lines = [l for l in content.splitlines() if l.strip()]
        self.assertEqual(len(lines), 3)  # cabecera + 2 tickets de la marca A
        self.assertIn("RA1", content)
        self.assertNotIn("RB1", content)

    def test_reporting_page_renders_for_agent(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse("reporting"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Reportes por entidad")


class SLAOrganizationTests(_BaseFixture):
    def setUp(self):
        super().setUp()
        from app.models import Organization, SLAPolicy
        self.tele = Organization.objects.create(name="Telefonica")
        self.tele_user = User.objects.create_user("tele@example.com", "Tele User", "secret")
        self.tele_user.role = self.end_role
        self.tele_user.organization = self.tele
        self.tele_user.save()
        self.p_tele = SLAPolicy.objects.create(name="Tele 2h", organization=self.tele, first_response_minutes=120)
        self.p_std = SLAPolicy.objects.create(name="Std 24h", first_response_minutes=1440)

    def test_org_specific_policy_wins_for_matching_requester(self):
        from app.services.sla import SLAService
        t = self.make_ticket(requester=self.tele_user, created_by=self.tele_user)
        self.assertEqual(SLAService.find_policy(t), self.p_tele)

    def test_other_client_falls_back_to_global_24h(self):
        from app.services.sla import SLAService
        t = self.make_ticket()  # customer sin organización
        self.assertEqual(SLAService.find_policy(t), self.p_std)

    def test_apply_policy_sets_2h_first_response_for_telefonica(self):
        from app.services.sla import SLAService
        t = self.make_ticket(requester=self.tele_user, created_by=self.tele_user)
        SLAService.apply_policy(t)
        t.refresh_from_db()
        minutes = (t.first_response_due_at - t.created_at).total_seconds() / 60
        self.assertAlmostEqual(minutes, 120, delta=1)

    def test_alarms_and_monitoring_are_sla_exempt(self):
        from app.services.sla import SLAService
        alarm = self.make_ticket(subject="ALARM: PRO-Recordia EC2 Error", requester=self.tele_user, created_by=self.tele_user)
        self.assertTrue(SLAService.is_sla_exempt(alarm))
        self.assertIsNone(SLAService.apply_policy(alarm))
        alarm.refresh_from_db()
        self.assertIsNone(alarm.first_response_due_at)  # sin SLA pese a ser Telefónica

        mon = self.make_ticket(subject="cpu high", monitoring=True)
        self.assertTrue(SLAService.is_sla_exempt(mon))


def _fake_embed(texts):
    """Embedding determinista 3-dim por palabras clave [fax, acceso/login, factura]."""
    vectors = []
    for t in texts:
        tl = t.lower()
        vectors.append([
            float(tl.count("fax")),
            float(tl.count("acceso") + tl.count("login")),
            float(tl.count("factura") + tl.count("invoice")),
        ])
    return vectors, "test-embed"


@override_settings(AI_REPLY_ASSIST_ENABLED=True, AI_SIMILAR_TICKETS_K=3)
class ReplyAssistTests(_BaseFixture):
    def _resolved(self, **kw):
        kw.setdefault("status", "resolved")
        return self.make_ticket(**kw)

    def test_find_similar_ranks_by_cosine_and_respects_resolved_only(self):
        from app.services.similar import SimilarTicketIndex

        fax = self._resolved(subject="No recibo faxes", description="el fax no llega al numero", brand=self.brand_a)
        login = self._resolved(subject="No puedo acceder", description="problema de login y acceso", brand=self.brand_a)
        invoice = self._resolved(subject="Duda factura", description="como exporto la factura", brand=self.brand_a)
        open_fax = self.make_ticket(subject="otro fax", description="fax", status="open")  # no resuelto -> excluido
        query = self.make_ticket(subject="Fax no recibido", description="no me llega el fax", status="open", brand=self.brand_a)

        with patch("app.services.ai.embed", side_effect=_fake_embed), \
             patch("app.services.ai._embedding_model", return_value="test-embed"):
            SimilarTicketIndex.backfill()
            results = SimilarTicketIndex.find_similar(query, k=2, resolved_only=True)

        ids = [t.id for t, _ in results]
        self.assertEqual(ids[0], fax.id)            # el más parecido es el de faxes
        self.assertNotIn(open_fax.id, ids)          # resolved_only excluye el abierto
        self.assertNotIn(query.id, ids)             # nunca a sí mismo

    def test_suggest_returns_drafts_and_similar_refs(self):
        from app.models import Comment
        from app.services.ticket_reply import TicketReplyService

        similar = self._resolved(subject="Fax no entra", description="el fax no llega", brand=self.brand_a)
        Comment.objects.create(ticket=similar, user=self.agent, content="Revisamos la ruta del fax y se reactivó.", is_public=True)
        ticket = self.make_ticket(subject="Mi fax no llega", description="no recibo faxes", brand=self.brand_a, language="es")
        Comment.objects.create(ticket=ticket, user=self.customer, content="Llevo dos días sin recibir faxes.", is_public=True)

        chat_payload = (
            {"language": "es", "drafts": [
                {"title": "Breve", "body": "Hemos revisado la ruta de su fax."},
                {"title": "Detallado", "body": "Estimado cliente, hemos revisado..."},
            ]},
            {"model": "gpt-4o-mini", "latency_ms": 500},
        )
        with patch("app.services.ai.embed", side_effect=_fake_embed), \
             patch("app.services.ai._embedding_model", return_value="test-embed"), \
             patch("app.services.ai.chat_json", return_value=chat_payload):
            from app.services.similar import SimilarTicketIndex
            SimilarTicketIndex.backfill()  # poblar embeddings de los candidatos
            result = TicketReplyService.suggest(ticket)

        self.assertEqual(result["language"], "es")
        self.assertEqual(len(result["drafts"]), 2)
        self.assertTrue(any(s["id"] == similar.id for s in result["similar"]))

    def test_can_use_gating(self):
        from app.services.ticket_reply import TicketReplyService

        with patch("app.services.ai.is_configured", return_value=True):
            self.assertTrue(TicketReplyService.can_use(self.agent))
            self.assertFalse(TicketReplyService.can_use(self.customer))  # no agente
            with override_settings(AI_REPLY_ASSIST_ENABLED=False):
                self.assertFalse(TicketReplyService.can_use(self.agent))  # flag off

    def test_endpoint_auth_and_agent_gating(self):
        ticket = self.make_ticket()
        url = reverse("ai_suggest_reply", args=[ticket.id])

        # sin login
        self.assertEqual(self.client.post(url, data="{}", content_type="application/json").status_code, 401)

        # cliente (no agente)
        self.client.force_login(self.customer)
        self.assertEqual(self.client.post(url, data="{}", content_type="application/json").status_code, 403)

        # agente, asistencia desactivada
        self.client.force_login(self.agent)
        with override_settings(AI_REPLY_ASSIST_ENABLED=False):
            self.assertEqual(self.client.post(url, data="{}", content_type="application/json").status_code, 503)

        # agente, OK (suggest mockeado)
        fake = {"language": "es", "drafts": [{"title": "t", "body": "b"}], "similar": [], "meta": {}}
        with patch("app.services.ticket_reply.TicketReplyService.can_use", return_value=True), \
             patch("app.services.ticket_reply.TicketReplyService.suggest", return_value=fake):
            resp = self.client.post(url, data="{}", content_type="application/json")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["data"]["drafts"][0]["body"], "b")


@override_settings(
    OUTBOUND_EMAIL_ENABLED=True,
    SATISFACTION_SURVEY_ENABLED=True,
    TICKETFLOW_PUBLIC_URL="https://ticketflow.example",
)
class SatisfactionSurveyTests(_BaseFixture):
    """Encuesta de satisfaccion nativa: oferta, voto y metricas.

    Las plantillas 'satisfaction_survey' y el catalogo de motivos los siembra la
    migracion 0047, asi que estan disponibles en la BBDD de test.
    """

    def _resolved_ticket(self, hours_ago=48, **overrides):
        values = {"status": "resolved", "brand": self.brand_a}
        values.update(overrides)
        ticket = self.make_ticket(**values)
        Ticket.objects.filter(id=ticket.id).update(
            resolved_at=timezone.now() - timedelta(hours=hours_ago)
        )
        ticket.refresh_from_db()
        return ticket

    def _offer(self, ticket):
        return SatisfactionService.offer(ticket)

    def _survey_url(self, rating):
        return reverse("satisfaction_survey", args=[rating.token])

    # ---------------- Oferta ----------------

    def test_offer_creates_offered_row_and_queues_email_with_vote_links(self):
        ticket = self._resolved_ticket()
        self.assertEqual(SatisfactionService.offer_pending_surveys(), 1)

        rating = SatisfactionRating.objects.get(ticket=ticket)
        self.assertEqual(rating.score, "offered")
        self.assertEqual(rating.source, SatisfactionRating.SOURCE_NATIVE)
        self.assertTrue(rating.token)
        self.assertIsNone(rating.zendesk_id)
        self.assertIsNotNone(rating.expires_at)
        self.assertIsNone(rating.responded_at)

        log = OutboundEmailLog.objects.get(ticket=ticket, template_key="satisfaction_survey")
        self.assertEqual(log.status, OutboundEmailLog.STATUS_QUEUED)
        # El token [Ticket #N] es lo que enlaza de vuelta una respuesta por correo.
        self.assertIn("[Ticket #%d]" % ticket.id, log.subject)
        self.assertIn("/satisfaction/%s/?score=good" % rating.token, log.payload["body_html"])
        self.assertIn("/satisfaction/%s/?score=bad" % rating.token, log.payload["body_html"])

    def test_offer_is_idempotent_per_resolution_cycle(self):
        ticket = self._resolved_ticket()
        SatisfactionService.offer_pending_surveys()
        self.assertEqual(SatisfactionService.offer_pending_surveys(), 0)
        self.assertEqual(SatisfactionRating.objects.filter(ticket=ticket).count(), 1)

    @override_settings(SATISFACTION_SURVEY_ENABLED=False)
    def test_kill_switch_blocks_every_offer(self):
        self._resolved_ticket()
        self.assertEqual(SatisfactionService.offer_pending_surveys(), 0)
        self.assertFalse(SatisfactionRating.objects.exists())

    def test_recent_resolutions_and_legacy_tickets_are_skipped(self):
        self._resolved_ticket(hours_ago=1)                                 # aun no toca
        legacy = self.make_ticket(status="resolved", brand=self.brand_a)   # resolved_at NULL
        self.assertIsNone(legacy.resolved_at)
        self.assertEqual(SatisfactionService.offer_pending_surveys(), 0)
        self.assertFalse(SatisfactionRating.objects.exists())

    def test_merged_and_deleted_tickets_are_never_surveyed(self):
        target = self.make_ticket()
        self._resolved_ticket(merged_into=target)
        self._resolved_ticket(is_deleted=True)
        self.assertEqual(SatisfactionService.offer_pending_surveys(), 0)
        self.assertFalse(SatisfactionRating.objects.exists())

    def test_no_offer_row_when_the_email_would_be_suppressed(self):
        # Buzon propio: el envio se suprimiria, y una oferta que nadie puede
        # responder falsearia la tasa de respuesta.
        own = User.objects.create_user("helpdesk@comuny.example", "Propio", "secret")
        self._resolved_ticket(requester=own)
        self.assertEqual(SatisfactionService.offer_pending_surveys(), 0)
        self.assertFalse(SatisfactionRating.objects.exists())

    @override_settings(TICKETFLOW_PUBLIC_URL="")
    def test_no_offer_without_a_public_url_for_the_vote_links(self):
        self._resolved_ticket()
        self.assertEqual(SatisfactionService.offer_pending_surveys(), 0)
        self.assertFalse(SatisfactionRating.objects.exists())

    def test_reopened_and_resolved_again_is_surveyed_again(self):
        ticket = self._resolved_ticket(hours_ago=96)
        SatisfactionService.offer_pending_surveys()
        first = SatisfactionRating.objects.get(ticket=ticket)
        SatisfactionService.record_vote(first, "good")
        # La oferta se emitio hace 72 h (24 h despues de aquella resolucion).
        SatisfactionRating.objects.filter(id=first.id).update(
            created_at=timezone.now() - timedelta(hours=72),
            offered_at=timezone.now() - timedelta(hours=72),
        )

        # Reapertura y nueva resolucion posterior: ciclo nuevo, encuesta nueva.
        Ticket.objects.filter(id=ticket.id).update(
            status="resolved", resolved_at=timezone.now() - timedelta(hours=48)
        )
        self.assertEqual(SatisfactionService.offer_pending_surveys(), 1)
        self.assertEqual(SatisfactionRating.objects.filter(ticket=ticket).count(), 2)

    def test_batch_limit_is_respected(self):
        for _ in range(3):
            self._resolved_ticket()
        with override_settings(SATISFACTION_SURVEY_BATCH=2):
            self.assertEqual(SatisfactionService.offer_pending_surveys(), 2)

    # ---------------- resolved_at ----------------

    def test_resolved_at_is_stamped_on_transition_and_refreshed_on_reresolution(self):
        from app.services.tickets import TicketService

        ticket = self.make_ticket(status="open")
        self.assertIsNone(ticket.resolved_at)

        # update_ticket reescribe TODOS los campos del payload (es el formulario
        # completo), asi que hay que mandar el ticket entero, no solo el estado.
        def _set_status(status):
            TicketService.update_ticket(self.agent, ticket.id, {
                "subject": ticket.subject,
                "description": ticket.description,
                "status": status,
                "priority": ticket.priority,
                "assignee_id": ticket.assignee_id,
            })

        _set_status("resolved")
        ticket.refresh_from_db()
        first_stamp = ticket.resolved_at
        self.assertIsNotNone(first_stamp)

        _set_status("open")
        _set_status("resolved")
        ticket.refresh_from_db()
        self.assertGreater(ticket.resolved_at, first_stamp)

    def test_direct_close_counts_as_resolution_for_the_survey_clock(self):
        from app.services.comments import CommentService

        ticket = self.make_ticket(status="open")
        CommentService.add_comment(ticket, self.agent, "cerrado", new_status="closed")
        ticket.refresh_from_db()
        self.assertIsNotNone(ticket.resolved_at)

    # ---------------- Pagina publica de voto ----------------

    def test_public_page_is_reachable_without_login_and_records_a_bad_vote(self):
        rating = self._offer(self._resolved_ticket())
        reason = SatisfactionReason.objects.filter(language="es", code="not_resolved").first()

        anonymous = Client()
        page = anonymous.get(self._survey_url(rating) + "?score=bad")
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, reason.label)

        voted = anonymous.post(self._survey_url(rating), {
            "score": "bad", "reason_id": reason.id, "comment": "  Sigo esperando  ",
        })
        self.assertEqual(voted.status_code, 200)
        rating.refresh_from_db()
        self.assertEqual(rating.score, "bad")
        self.assertEqual(rating.reason_choice_id, reason.id)
        self.assertEqual(rating.comment, "Sigo esperando")
        self.assertIsNotNone(rating.responded_at)

    def test_vote_can_be_rectified_within_the_window_and_clears_the_reason(self):
        rating = self._offer(self._resolved_ticket())
        reason = SatisfactionReason.objects.filter(language="es").first()
        SatisfactionService.record_vote(rating, "bad", reason=reason)

        Client().post(self._survey_url(rating), {"score": "good"})
        rating.refresh_from_db()
        self.assertEqual(rating.score, "good")
        self.assertIsNone(rating.reason_choice_id)

    def test_vote_is_attributed_to_the_assignee_at_vote_time(self):
        ticket = self._resolved_ticket()
        rating = self._offer(ticket)
        self.assertEqual(rating.assignee_id, self.agent.id)

        other = User.objects.create_user("agent2@example.com", "Agent 2", "secret")
        Ticket.objects.filter(id=ticket.id).update(assignee=other)
        Client().post(self._survey_url(rating), {"score": "good"})
        rating.refresh_from_db()
        self.assertEqual(rating.assignee_id, other.id)

    def test_expired_link_shows_the_expiry_notice_and_rejects_the_vote(self):
        rating = self._offer(self._resolved_ticket())
        SatisfactionRating.objects.filter(id=rating.id).update(
            expires_at=timezone.now() - timedelta(days=1)
        )
        anonymous = Client()
        self.assertContains(anonymous.get(self._survey_url(rating)), "caducado")

        anonymous.post(self._survey_url(rating), {"score": "good"})
        rating.refresh_from_db()
        self.assertEqual(rating.score, "offered")

    def test_invalid_score_does_not_touch_the_rating(self):
        rating = self._offer(self._resolved_ticket())
        page = Client().post(self._survey_url(rating), {"score": "regular"})
        self.assertEqual(page.status_code, 200)
        rating.refresh_from_db()
        self.assertEqual(rating.score, "offered")

    def test_unknown_token_returns_404(self):
        self.assertEqual(Client().get("/satisfaction/inventado/").status_code, 404)

    # ---------------- Metricas ----------------

    def _reporting(self):
        # /reporting/data/ cachea el payload 60 s con una clave que solo lleva marca
        # y fechas, asi que sin limpiar se arrastraria el resultado de otro test.
        from django.core.cache import cache

        cache.clear()
        today = timezone.localdate()
        return self.client.get(
            "/reporting/data/?from=%s&to=%s" % (today - timedelta(days=1), today)
        ).json()

    def test_offers_without_answer_do_not_move_the_csat(self):
        self.client.force_login(self.admin)
        SatisfactionService.record_vote(self._offer(self._resolved_ticket()), "good")
        self._offer(self._resolved_ticket())  # se queda en 'offered'

        kpis = self._reporting()["kpis"]
        self.assertEqual(kpis["satisfaction_good"], 1)
        self.assertEqual(kpis["satisfaction_bad"], 0)
        self.assertEqual(kpis["satisfaction_pct"], 100)
        # La oferta sin responder solo asoma en la tasa de respuesta.
        self.assertEqual(kpis["satisfaction_offered"], 2)
        self.assertEqual(kpis["satisfaction_answered"], 1)
        self.assertEqual(kpis["satisfaction_response_rate_pct"], 50)

    def test_agent_csat_hides_samples_below_the_minimum(self):
        self.client.force_login(self.admin)
        for _ in range(2):
            SatisfactionService.record_vote(self._offer(self._resolved_ticket()), "good")
        self.assertEqual(self._reporting()["satisfaction"]["by_agent"], [])

        SatisfactionService.record_vote(self._offer(self._resolved_ticket()), "bad")
        rows = self._reporting()["satisfaction"]["by_agent"]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["total"], 3)
        self.assertEqual(rows[0]["pct"], 67)

    def test_reason_ranking_merges_catalog_and_zendesk_free_text(self):
        self.client.force_login(self.admin)
        reason = SatisfactionReason.objects.filter(language="es", code="slow_resolution").first()
        SatisfactionService.record_vote(self._offer(self._resolved_ticket()), "bad", reason=reason)
        # Fila al estilo de lo importado de Zendesk: motivo en texto libre.
        SatisfactionRating.objects.create(
            ticket=self._resolved_ticket(), zendesk_id=999001, score="bad",
            reason="Motivo heredado", source=SatisfactionRating.SOURCE_ZENDESK,
            created_at=timezone.now(),
        )

        labels = {r["label"]: r["n"] for r in self._reporting()["satisfaction"]["reasons"]}
        self.assertEqual(labels.get(reason.label), 1)
        self.assertEqual(labels.get("Motivo heredado"), 1)

    def test_native_and_zendesk_ratings_coexist_with_nullable_zendesk_id(self):
        # zendesk_id es unique y nullable: varias filas nativas a NULL tienen que
        # convivir con las importadas sin romper el indice unico.
        SatisfactionRating.objects.create(
            ticket=self.make_ticket(), zendesk_id=555001, score="good",
            source=SatisfactionRating.SOURCE_ZENDESK, created_at=timezone.now(),
        )
        self._offer(self._resolved_ticket())
        self._offer(self._resolved_ticket())
        self.assertEqual(SatisfactionRating.objects.filter(zendesk_id__isnull=True).count(), 2)
        self.assertEqual(SatisfactionRating.objects.count(), 3)
