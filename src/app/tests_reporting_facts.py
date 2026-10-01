"""Pruebas de las tablas de hechos de /reporting/.

La prueba central es de **paridad**: cada zona del informe tiene que devolver
exactamente el mismo payload leyendo los hechos precalculados que
reconstruyendolos desde la auditoria. Es lo que sostiene que la optimizacion no
cambia ningun numero publicado.
"""

from datetime import datetime, time, timedelta
from unittest import mock

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


class UpsertBackendCompatibilityTests(_FactFixture):
    """El upsert de TicketFact tiene que valer en MySQL, que es DEV y produccion.

    La suite corre sobre SQLite, que admite `unique_fields` en bulk_create con
    update_conflicts. MySQL no: resuelve el upsert con ON DUPLICATE KEY UPDATE y
    levanta NotSupportedError si se le indica la columna. Eso hizo que la tarea
    refresh_reporting_facts pasara los tests y fallara cada 5 minutos en DEV, asi
    que aqui se simula el flag del motor en vez de confiar en el backend local.
    """

    def _bulk_kwargs_with_target(self, supported):
        captured = {}
        original = TicketFact.objects.bulk_create

        def spy(objs, **kwargs):
            captured.update(kwargs)
            return original(objs, **kwargs)

        with mock.patch.object(
            type(connection.features), "supports_update_conflicts_with_target", supported,
        ), mock.patch.object(TicketFact.objects, "bulk_create", side_effect=spy):
            refresh_ticket_facts([self.simple.id])
        return captured

    def test_mysql_no_recibe_unique_fields(self):
        kwargs = self._bulk_kwargs_with_target(False)
        self.assertTrue(kwargs["update_conflicts"])
        self.assertNotIn("unique_fields", kwargs)

    def test_postgres_y_sqlite_si_lo_reciben(self):
        kwargs = self._bulk_kwargs_with_target(True)
        self.assertEqual(kwargs["unique_fields"], ["ticket"])


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
        # publica comparativa. assignee_activity lee ademas la cola al cierre
        # (_unsolved_at): consultas fijas por periodo, no por ticket.
        budgets = {
            "executive": 25, "tickets": 18, "efficiency": 16, "assignee_activity": 17,
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


class ReportingRangeTests(_FactFixture):
    """El selector de periodo tiene que mover de verdad lo que pinta cada zona."""

    def _solved_ids(self, days):
        _from, _to, start_dt, end_dt = self._range(days)
        base = reporting_metrics.live_ticket_queryset(ReportFilters.from_params({}))
        return {ticket.id for ticket in reporting_metrics._solved_tickets(base, start_dt, end_dt)}

    def _auto_close(self, ticket, days_ago):
        """Lo que hace una automatizacion de cierre: resuelto -> cerrado, hoy."""
        self._event(ticket, "status", "resolved", "closed", days_ago=days_ago)
        Ticket.objects.filter(id=ticket.id).update(
            status="closed", closed_at=self.now - timedelta(days=days_ago),
        )

    def test_closing_an_already_resolved_ticket_is_not_a_new_resolution(self):
        self._auto_close(self.simple, days_ago=1)
        for with_facts in (False, True):
            if with_facts:
                rebuild_all_facts()
            with self.subTest(with_facts=with_facts):
                # Resuelto hace 5 dias: entra en la ultima semana, no en los ultimos 3.
                self.assertNotIn(self.simple.id, self._solved_ids(3))
                self.assertIn(self.simple.id, self._solved_ids(7))

    def test_zendesk_closed_at_does_not_override_the_audited_resolution(self):
        """El sync iguala closed_at al updated_at de Zendesk, que se mueve solo."""
        Ticket.objects.filter(id=self.simple.id).update(
            status="closed", resolved_at=None, closed_at=self.now - timedelta(days=1),
        )
        self.simple.refresh_from_db()
        fact = _lifecycle_facts_from_source([self.simple], now=self.now)[self.simple.id]
        self.assertEqual(
            fact["full_resolution_at"].date(), (self.now - timedelta(days=5)).date()
        )
        self.assertNotIn(self.simple.id, self._solved_ids(3))

    def test_closed_at_still_resolves_tickets_without_audit(self):
        self.assertIn(self.imported.id, self._solved_ids(30))
        self.assertNotIn(self.imported.id, self._solved_ids(7))

    def test_a_ticket_resolved_after_its_facts_were_stored_is_reported(self):
        rebuild_all_facts()
        self._event(self.untouched, "status", "open", "resolved", days_ago=0)
        Ticket.objects.filter(id=self.untouched.id).update(
            status="resolved", resolved_at=self.now, updated_at=self.now + timedelta(seconds=1),
        )
        self.assertIn(self.untouched.id, self._solved_ids(1))

    def test_backlog_samples_follow_the_selected_range(self):
        rebuild_all_facts()
        filters = ReportFilters.from_params({})
        week = SECTION_BUILDERS["backlog"](filters, *self._range(7))
        quarter = SECTION_BUILDERS["backlog"](filters, *self._range(90))
        self.assertEqual(len(week["charts"]["daily_by_status"]), 7)
        self.assertEqual(len(quarter["charts"]["daily_by_status"]), 90)
        self.assertEqual(len(week["charts"]["weekly_by_status"]), 2)
        self.assertEqual(len(quarter["charts"]["weekly_by_status"]), 13)
        year = SECTION_BUILDERS["backlog"](filters, *self._range(365))
        self.assertEqual(len(year["charts"]["daily_by_status"]), 90, "techo de cortes diarios")

    def test_monthly_series_stay_inside_the_range(self):
        rebuild_all_facts()
        filters = ReportFilters.from_params({})
        date_from, date_to, start_dt, end_dt = self._range(7)
        months = {f"{date_from:%Y-%m}", f"{date_to:%Y-%m}"}
        satisfaction = SECTION_BUILDERS["satisfaction"](filters, date_from, date_to, start_dt, end_dt)
        for row in satisfaction["charts"]["score_rated_by_month"]:
            self.assertIn(row["month"], months)
        sla = SECTION_BUILDERS["sla"](filters, date_from, date_to, start_dt, end_dt)
        for row in sla["charts"]["achievement_by_month"]:
            self.assertIn(row["month"], months)

    def test_assigned_unsolved_is_the_queue_at_the_end_of_the_range(self):
        rebuild_all_facts()
        filters = ReportFilters.from_params({})
        today = SECTION_BUILDERS["assignee_activity"](filters, *self._range(7))
        kpis = {row["key"]: row["value"] for row in today["kpis"]}
        # reabierto + heredado + sin tocar + con SLA, todos con agente asignado
        self.assertEqual(kpis["assigned_unsolved"], 4)
        today_end = timezone.localdate()
        past = today_end - timedelta(days=28)
        past_payload = SECTION_BUILDERS["assignee_activity"](
            filters, past - timedelta(days=6), past,
            _aware(datetime.combine(past - timedelta(days=6), time.min)),
            _aware(datetime.combine(past, time.max)),
        )
        past_kpis = {row["key"]: row["value"] for row in past_payload["kpis"]}
        # Hace 28 dias solo existian el reabierto (no se resolvio hasta el dia
        # -25) y el importado (cerrado el -18): ambos seguian en cola.
        self.assertEqual(past_kpis["assigned_unsolved"], 2)
