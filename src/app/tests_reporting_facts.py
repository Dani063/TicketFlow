"""Pruebas de las tablas de hechos de /reporting/.

La prueba central es de **paridad**: cada zona del informe tiene que devolver
exactamente el mismo payload leyendo los hechos precalculados que
reconstruyendolos desde la auditoria. Es lo que sostiene que la optimizacion no
cambia ningun numero publicado.
"""

from datetime import datetime, time, timedelta

from django.core.management import call_command
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from app.models import (
    Brand, Comment, ProductLine, ReportingFactState, Role, SatisfactionRating,
    Ticket, TicketEvent, TicketFact, TicketStatusInterval, User,
)
from app.services import reporting_metrics
from app.services.reporting_facts import (
    rebuild_all_facts, refresh_stale_facts, refresh_ticket_facts, stale_ticket_ids,
)
from app.services.reporting_metrics import (
    LIFECYCLE_EVENT_NAMES, SECTION_BUILDERS, ReportFilters,
    _lifecycle_facts, _lifecycle_facts_from_source, _state_at, _status_timeline,
    _ticket_events, build_reporting_section,
)


def _aware(value):
    return timezone.make_aware(value, timezone.get_current_timezone())


class _FactFixture(TestCase):
    """Un historico pequeno pero con las formas que rompen los atajos."""

    def setUp(self):
        self.end_role = Role.objects.create(role_name="End user")
        self.agent_role = Role.objects.create(role_name="agent")
        self.customer = User.objects.create_user("cli@example.com", "Cliente", "secret")
        self.customer.role = self.end_role
        self.customer.save(update_fields=["role"])
        self.agent = User.objects.create_user("age@example.com", "Agente", "secret")
        self.agent.role = self.agent_role
        self.agent.save(update_fields=["role"])

        self.brand = Brand.objects.create(
            name="Comuny", support_email="hd@comuny.example", mailbox_type="m365", language="es",
        )
        self.product = ProductLine.objects.get(code="recordia")
        self.now = timezone.now()

        # 1) Ciclo normal: abierto -> pendiente -> resuelto, con respuestas.
        self.simple = self._ticket("Simple", days_ago=20, status="resolved")
        Ticket.objects.filter(id=self.simple.id).update(
            resolved_at=self.now - timedelta(days=5),
        )
        self.simple.refresh_from_db()
        self._event(self.simple, "status", "open", "pending", days_ago=14)
        self._event(self.simple, "status", "pending", "resolved", days_ago=5)
        self._comment(self.simple, self.agent, days_ago=16, public=True)
        self._comment(self.simple, self.agent, days_ago=15, public=False)

        # 2) Reabierto: el tramo sin resolver no es uno solo.
        self.reopened = self._ticket("Reabierto", days_ago=30, status="open")
        self._event(self.reopened, "status", "open", "resolved", days_ago=25)
        self._event(self.reopened, "status", "resolved", "open", days_ago=12)
        self._comment(self.reopened, self.agent, days_ago=26, public=True)

        # 3) Importado de Zendesk: sin auditorias, solo closed_at.
        self.imported = self._ticket("Importado", days_ago=40, status="closed")
        Ticket.objects.filter(id=self.imported.id).update(
            closed_at=self.now - timedelta(days=18),
        )
        self.imported.refresh_from_db()

        # 4) Auditoria en castellano, como los imports antiguos.
        self.legacy = self._ticket("Heredado", days_ago=22, status="pending")
        self._event(self.legacy, "estado", "open", "pending", days_ago=9)

        # 5) Sigue abierto y nunca se ha tocado: el reloj de espera corre.
        self.untouched = self._ticket("Sin tocar", days_ago=6, status="open")

        # 6) Con relojes de SLA, para build_sla.
        self.with_sla = self._ticket("Con SLA", days_ago=10, status="open")
        Ticket.objects.filter(id=self.with_sla.id).update(
            first_response_due_at=self.now - timedelta(days=9),
            resolution_due_at=self.now + timedelta(days=2),
            sla_breached_at=self.now - timedelta(days=8),
            first_response_met=False,
        )
        self.with_sla.refresh_from_db()

        SatisfactionRating.objects.create(
            ticket=self.simple, assignee=self.agent, score="good",
            created_at=self.now - timedelta(days=4),
        )
        SatisfactionRating.objects.create(
            ticket=self.imported, assignee=self.agent, score="bad",
            created_at=self.now - timedelta(days=17),
        )

    def _ticket(self, subject, days_ago, status):
        ticket = Ticket.objects.create(
            subject=subject, description="d", status=status, priority="normal",
            requester=self.customer, assignee=self.agent, created_by=self.customer,
            product_line=self.product, brand=self.brand, channel="email", type="question",
        )
        created = self.now - timedelta(days=days_ago)
        Ticket.objects.filter(id=ticket.id).update(created_at=created, updated_at=created)
        ticket.refresh_from_db()
        return ticket

    def _event(self, ticket, field, old, new, days_ago):
        return TicketEvent.objects.create(
            ticket=ticket, actor=self.agent, field_name=field,
            old_value=old, new_value=new, created_at=self.now - timedelta(days=days_ago),
        )

    def _comment(self, ticket, user, days_ago, public):
        comment = Comment.objects.create(
            ticket=ticket, user=user, content="c", is_public=public,
        )
        Comment.objects.filter(id=comment.id).update(
            created_at=self.now - timedelta(days=days_ago)
        )
        return comment

    def _range(self, days=30):
        today = timezone.localdate()
        date_from = today - timedelta(days=days - 1)
        return (
            date_from, today,
            _aware(datetime.combine(date_from, time.min)),
            _aware(datetime.combine(today, time.max)),
        )


class StatusTimelineTests(_FactFixture):
    def test_timeline_matches_state_at_on_every_shape(self):
        """_status_timeline no puede divergir de _state_at en ningun corte.

        _state_at es la definicion de referencia del estado historico. Si los
        tramos persistidos dicen otra cosa, el backlog publica numeros falsos.
        """
        tickets = list(Ticket.objects.all())
        events = _ticket_events(
            [t.id for t in tickets], field_names=LIFECYCLE_EVENT_NAMES, include_actor=False,
        )
        samples = [self.now - timedelta(days=d) for d in (45, 35, 28, 20, 13, 8, 4, 1)]
        samples.append(self.now + timedelta(days=1))

        for ticket in tickets:
            timeline = _status_timeline(ticket, events.get(ticket.id, []))
            with self.subTest(ticket=ticket.subject):
                for index in range(len(timeline) - 1):
                    self.assertEqual(
                        timeline[index][2], timeline[index + 1][1],
                        "los tramos tienen que ser contiguos",
                    )
                    self.assertLess(
                        timeline[index][1], timeline[index][2],
                        "no puede haber tramos de duracion cero",
                    )
                for sample in samples:
                    expected = _state_at(ticket, events.get(ticket.id, []), sample)
                    actual = next(
                        (state for state, start, end in timeline
                         if start <= sample and (end is None or sample < end)),
                        None,
                    )
                    self.assertEqual(actual, expected, f"corte {sample:%Y-%m-%d}")

    def test_reopened_ticket_produces_two_unsolved_spans(self):
        refresh_ticket_facts([self.reopened.id])
        spans = list(
            TicketStatusInterval.objects
            .filter(ticket=self.reopened, status="open")
            .order_by("started_at")
        )
        self.assertEqual(len(spans), 2)
        self.assertIsNotNone(spans[0].ended_at)
        self.assertIsNone(spans[1].ended_at, "el tramo vigente queda abierto")


class LifecycleFactParityTests(_FactFixture):
    KEYS = [
        "agent_replies", "first_reply_at", "first_reply_hours", "first_assignment_at",
        "first_assignment_hours", "first_resolution_at", "first_resolution_hours",
        "full_resolution_at", "full_resolution_hours", "last_assignment_at",
        "assignment_to_resolution_hours", "requester_wait_hours", "assignee_stations",
        "group_stations", "reopens",
    ]

    def test_stored_facts_match_recomputed_facts(self):
        rebuild_all_facts()
        tickets = list(Ticket.objects.all())
        stored = _lifecycle_facts(tickets, now=self.now)
        fresh = _lifecycle_facts_from_source(tickets, now=self.now)
        for ticket in tickets:
            for key in self.KEYS:
                with self.subTest(ticket=ticket.subject, key=key):
                    self.assertEqual(stored[ticket.id][key], fresh[ticket.id][key])

    def test_requester_wait_keeps_running_after_the_fact_was_stored(self):
        """El reloj de espera de un ticket abierto no se congela al persistirlo.

        Es la razon de guardar (segundos acumulados, abierto desde) en vez de un
        unico numero: el valor tiene que seguir creciendo entre refrescos.
        """
        refresh_ticket_facts([self.untouched.id])
        later = self.now + timedelta(hours=10)
        early = _lifecycle_facts([self.untouched], now=self.now)
        late = _lifecycle_facts([self.untouched], now=later)
        self.assertAlmostEqual(
            late[self.untouched.id]["requester_wait_hours"]
            - early[self.untouched.id]["requester_wait_hours"],
            10.0, places=2,
        )

    def test_missing_facts_fall_back_instead_of_reporting_zero(self):
        """Sin backfill, el informe se calcula igual en vez de salir vacio."""
        self.assertEqual(TicketFact.objects.count(), 0)
        facts = _lifecycle_facts(list(Ticket.objects.all()), now=self.now)
        self.assertEqual(len(facts), Ticket.objects.count())


class SectionParityTests(_FactFixture):
    """Cada zona tiene que dar el mismo payload con y sin hechos calculados."""

    def test_every_section_is_identical_with_and_without_facts(self):
        date_from, date_to, start_dt, end_dt = self._range()
        filters = ReportFilters.from_params({})

        legacy = {
            section: build_reporting_section(section, filters, date_from, date_to, start_dt, end_dt)
            for section in SECTION_BUILDERS
        }
        self.assertEqual(TicketFact.objects.count(), 0, "el camino previo no usa hechos")

        rebuild_all_facts()
        self.assertEqual(TicketFact.objects.count(), Ticket.objects.count())

        for section in SECTION_BUILDERS:
            with self.subTest(section=section):
                current = build_reporting_section(
                    section, filters, date_from, date_to, start_dt, end_dt
                )
                self.assertEqual(current, legacy[section])

    def test_product_filter_is_identical_with_and_without_facts(self):
        date_from, date_to, start_dt, end_dt = self._range()
        filters = ReportFilters.from_params({"product": str(self.product.id)})
        legacy = {
            section: build_reporting_section(section, filters, date_from, date_to, start_dt, end_dt)
            for section in SECTION_BUILDERS
        }
        rebuild_all_facts()
        for section in SECTION_BUILDERS:
            with self.subTest(section=section):
                self.assertEqual(
                    build_reporting_section(
                        section, filters, date_from, date_to, start_dt, end_dt
                    ),
                    legacy[section],
                )

    def test_backlog_counts_the_reopened_ticket_as_unsolved_today(self):
        rebuild_all_facts()
        date_from, date_to, start_dt, end_dt = self._range()
        payload = SECTION_BUILDERS["backlog"](
            ReportFilters.from_params({}), date_from, date_to, start_dt, end_dt
        )
        today = payload["charts"]["daily_by_status"][-1]
        # reabierto + heredado(pending) + sin tocar + con SLA
        self.assertEqual(today["open"] + today["pending"], 4)


class FactFreshnessTests(_FactFixture):
    def test_new_and_changed_tickets_are_detected_as_stale(self):
        rebuild_all_facts()
        self.assertEqual(stale_ticket_ids(), [])

        fresh = self._ticket("Nuevo", days_ago=0, status="open")
        self.assertIn(fresh.id, stale_ticket_ids())

        refresh_stale_facts()
        self.assertEqual(stale_ticket_ids(), [])

        self.simple.status = "closed"
        self.simple.save(update_fields=["status", "updated_at"])
        self.assertIn(self.simple.id, stale_ticket_ids())

    def test_a_comment_written_without_touching_the_ticket_is_still_detected(self):
        """Red de seguridad: auditorias escritas sin pasar por Ticket.save()."""
        rebuild_all_facts()
        self.assertEqual(stale_ticket_ids(), [])
        TicketEvent.objects.create(
            ticket=self.simple, actor=self.agent, field_name="status",
            old_value="resolved", new_value="open", created_at=timezone.now(),
        )
        self.assertIn(self.simple.id, stale_ticket_ids())

    def test_refresh_is_idempotent(self):
        rebuild_all_facts()
        intervals = TicketStatusInterval.objects.count()
        facts = TicketFact.objects.count()
        rebuild_all_facts()
        self.assertEqual(TicketStatusInterval.objects.count(), intervals)
        self.assertEqual(TicketFact.objects.count(), facts)

    def test_partial_backfill_does_not_pass_as_complete(self):
        """Con los hechos a medias hay que seguir por el camino antiguo.

        Si bastara con «hay alguna fila», una pasada del refresco todavia en
        curso haria que el backlog publicase una cola incompleta como si fuera
        el dato bueno.
        """
        refresh_ticket_facts([self.simple.id])  # solo un ticket de seis
        self.assertTrue(TicketStatusInterval.objects.exists())
        self.assertFalse(reporting_metrics._facts_available())

        date_from, date_to, start_dt, end_dt = self._range()
        payload = SECTION_BUILDERS["backlog"](
            ReportFilters.from_params({}), date_from, date_to, start_dt, end_dt
        )
        today = payload["charts"]["daily_by_status"][-1]
        self.assertEqual(today["open"] + today["pending"], 4, "la cola real, no la calculada a medias")

        rebuild_all_facts()
        self.assertTrue(reporting_metrics._facts_available())

    def test_a_complete_refresh_pass_marks_the_facts_usable(self):
        self.assertFalse(reporting_metrics._facts_available())
        refresh_stale_facts()
        self.assertTrue(reporting_metrics._facts_available())
        state = ReportingFactState.objects.get()
        self.assertEqual(state.tickets, Ticket.objects.count())

    def test_backfill_command_populates_both_tables(self):
        call_command("rebuild_reporting_facts", verbosity=0)
        self.assertEqual(TicketFact.objects.count(), Ticket.objects.count())
        self.assertTrue(TicketStatusInterval.objects.exists())


class QueryBudgetTests(_FactFixture):
    """Guardas contra la regresion que motivo todo esto: consultas por fila."""

    def test_sections_do_not_issue_a_query_per_ticket(self):
        rebuild_all_facts()
        date_from, date_to, start_dt, end_dt = self._range()
        filters = ReportFilters.from_params({})
        # Techos ajustados a lo que cuesta hoy cada zona (mas un pequeno margen).
        # Son numeros constantes por diseno: no deben crecer con el numero de
        # tickets, comentarios ni eventos. Si una de estas cifras sube al tocar
        # el reporting, es que ha vuelto a colarse una consulta por fila.
        # build_reporting_section calcula ademas el periodo anterior, de ahi que
        # casi todas doblen el coste de su builder; backlog no, porque no
        # publica comparativa.
        budgets = {
            "executive": 25, "tickets": 18, "efficiency": 16, "assignee_activity": 15,
            "agent_updates": 14, "unsolved": 14, "backlog": 6, "satisfaction": 15,
            "sla": 9,
        }
        for section, budget in budgets.items():
            with self.subTest(section=section):
                with CaptureQueriesContext(connection) as ctx:
                    build_reporting_section(
                        section, filters, date_from, date_to, start_dt, end_dt
                    )
                self.assertLessEqual(
                    len(ctx.captured_queries), budget,
                    f"{section} ha hecho {len(ctx.captured_queries)} consultas",
                )

    def test_comment_loader_does_not_defer_the_field_is_agent_reads(self):
        """is_agent() lee is_superuser: si .only() no lo trae, es una query por fila."""
        for index in range(6):
            self._comment(self.simple, self.agent, days_ago=index + 1, public=True)

        with CaptureQueriesContext(connection) as ctx:
            grouped = reporting_metrics._agent_comments([self.simple.id])
            self.assertTrue(grouped[self.simple.id])
        self.assertEqual(len(ctx.captured_queries), 1)
