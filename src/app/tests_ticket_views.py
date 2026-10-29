import csv
import io
from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import Brand, Group, Role, SatisfactionRating, Ticket, User


@override_settings(ALLOWED_HOSTS=['testserver'], OUTBOUND_EMAIL_ENABLED=False)
class TicketViewConsistencyTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        agent_role = Role.objects.create(role_name='agent')
        cls.group = Group.objects.create(group_name='Soporte')
        cls.agent = User.objects.create_user('views-agent@example.test', 'Agente')
        cls.other = User.objects.create_user('views-other@example.test', 'Otro')
        for user in (cls.agent, cls.other):
            user.role, user.group = agent_role, cls.group
            user.save(update_fields=['role', 'group'])
        cls.customer = User.objects.create_user('views-customer@example.test', 'Cliente')
        cls.customer.role = Role.objects.create(role_name='end user')
        cls.customer.save(update_fields=['role'])
        cls.brand = Brand.objects.create(name='Marca')

    def setUp(self):
        cache.clear()
        self.client.force_login(self.agent)

    def ticket(self, **kwargs):
        values = dict(subject='Prueba de vistas', description='Prueba', status='open',
                      requester=self.customer, created_by=self.customer, assignee=self.agent,
                      brand=self.brand)
        values.update(kwargs)
        return Ticket.objects.create(**values)

    def rows(self, view, **kwargs):
        response = self.client.get('/tickets/filter/', {'view': view, 'page_size': 50, **kwargs})
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_home_and_personal_queue_share_old_assigned_non_task_work(self):
        old = self.ticket()
        Ticket.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=100))
        hold = self.ticket(status='on_hold')
        self.ticket(type='task')
        self.ticket(status='resolved')
        self.ticket(status='closed')
        self.ticket(requester=self.agent, assignee=self.other)
        cc = self.ticket(assignee=self.other)
        cc.ccs.add(self.agent)
        self.ticket(is_deleted=True)
        for view in ['home_all', 'unsolved_no_tareas']:
            payload = self.rows(view)
            self.assertCountEqual([t['id'] for t in payload['tickets']], [old.id, hold.id])
            self.assertFalse(payload['applied_filters']['date_range_applied'])
        self.assertEqual(self.client.get('/api/home-stats/').json()['pendientes_count'], 2)

    def test_on_hold_is_in_unsolved_rows_counts_and_exports(self):
        hold = self.ticket(status='on_hold')
        self.ticket(status='resolved')
        self.ticket(status='closed')
        counts = self.client.get('/tickets/filter/', {
            'counts': 1, 'counts_only': 1, 'force_counts': 1,
        }).json()['filtros']
        for view in ['unsolved_no_tareas', 'all_unsolved_no_tareas', 'no_resueltos', 'unsolved_groups']:
            with self.subTest(view=view):
                self.assertEqual([t['id'] for t in self.rows(view)['tickets']], [hold.id])
                self.assertEqual(counts[view], 1)
                response = self.client.get('/tickets/export.csv', {'view': view})
                rows = csv.reader(io.StringIO(b''.join(response.streaming_content).decode('utf-8-sig')))
                self.assertEqual([int(row[0]) for row in rows if len(row) == 11 and row[0].isdigit()], [hold.id])

    def test_queue_counts_rows_and_csv_ignore_creation_range_and_keep_brand(self):
        old = self.ticket(status='pending', service='ecomfax')
        Ticket.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=100))
        self.ticket(brand=Brand.objects.create(name='Otra'))
        filters = {'from': str(timezone.localdate()), 'to': str(timezone.localdate()), 'brand_id': self.brand.id}
        counts = self.client.get('/tickets/filter/', {**filters, 'counts': 1, 'counts_only': 1, 'force_counts': 1}).json()['filtros']
        for view in ['unsolved_no_tareas', 'all_unsolved_no_tareas', 'pendientes', 'no_resueltos', 'unsolved_groups']:
            with self.subTest(view=view):
                payload = self.rows(view, **filters)
                self.assertEqual([t['id'] for t in payload['tickets']], [old.id])
                self.assertEqual(counts[view], 1)
                response = self.client.get('/tickets/export.csv', {'view': view, **filters})
                self.assertEqual(response.status_code, 200)
                content = b''.join(response.streaming_content).decode('utf-8-sig')
                rows = csv.reader(io.StringIO(content))
                self.assertEqual([int(row[0]) for row in rows if len(row) == 11 and row[0].isdigit()], [old.id])

    def test_historical_views_still_apply_dates_independently_of_active_queue(self):
        recent = self.ticket(status='resolved')
        old = self.ticket(status='resolved')
        Ticket.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=100))
        payload = self.rows('resueltos')
        self.assertEqual([t['id'] for t in payload['tickets']], [recent.id])
        self.assertTrue(payload['applied_filters']['date_range_applied'])
        counts = self.client.get('/tickets/filter/', {'view': 'no_resueltos', 'counts': 1, 'counts_only': 1, 'force_counts': 1}).json()['filtros']
        self.assertEqual(counts['resueltos'], 1)
        self.assertEqual(counts['no_resueltos'], 0)

    def assert_exports(self, filters, expected_ids, expected_from):
        response = self.client.get('/tickets/export.csv', filters)
        self.assertEqual(response.status_code, 200)
        rows = list(csv.reader(io.StringIO(b''.join(response.streaming_content).decode('utf-8-sig'))))
        self.assertCountEqual([int(row[0]) for row in rows if len(row) == 11 and row[0].isdigit()], expected_ids)
        self.assertIn(['Desde', expected_from], rows)
        captured = {}

        def render(tickets, metadata):
            captured.update(ids=[ticket.id for ticket in tickets], metadata=metadata)
            return b'%PDF-1.4 test'

        with patch('app.services.ticket_exports.build_tickets_pdf', side_effect=render):
            response = self.client.get('/tickets/export.pdf', filters)
        self.assertEqual(response.status_code, 200)
        self.assertCountEqual(captured['ids'], expected_ids)
        self.assertEqual(captured['metadata']['date_from'], expected_from)
        self.assertEqual(captured['metadata']['total'], len(expected_ids))

    def test_explicit_range_filters_queues_counts_totals_and_exports(self):
        old = self.ticket(status='pending')
        Ticket.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=100))
        recent = self.ticket(status='pending')
        self.ticket(brand=Brand.objects.create(name='Fuera del filtro'))
        start = timezone.localdate() - timedelta(days=30)
        filters = {'date_mode': 'range', 'from': str(start), 'to': str(timezone.localdate()), 'brand_id': self.brand.id}
        counts = self.client.get('/tickets/filter/', {**filters, 'counts': 1, 'counts_only': 1}).json()['filtros']
        for view in ['unsolved_no_tareas', 'all_unsolved_no_tareas', 'pendientes', 'no_resueltos', 'unsolved_groups']:
            with self.subTest(view=view):
                payload = self.rows(view, fast=1, **filters)
                self.assertEqual([t['id'] for t in payload['tickets']], [recent.id])
                self.assertTrue(payload['applied_filters']['date_range_applied'])
                self.assertEqual(counts[view], 1)
                total = self.rows(view, total_only=1, **filters)
                self.assertEqual(total['pagination']['total'], 1)
                self.assert_exports({'view': view, **filters}, [recent.id], start.strftime('%d/%m/%Y'))

    def test_custom_queue_range_is_normalized_and_can_select_old_tickets(self):
        old = self.ticket()
        old_date = timezone.localdate() - timedelta(days=100)
        Ticket.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=100))
        self.ticket()
        start, end = old_date - timedelta(days=1), old_date + timedelta(days=1)
        filters = {'view': 'all_unsolved_no_tareas', 'date_mode': 'range', 'from': str(end), 'to': str(start)}
        payload = self.rows(**filters)
        self.assertEqual([t['id'] for t in payload['tickets']], [old.id])
        self.assertEqual((payload['applied_filters']['from'], payload['applied_filters']['to']), (str(start), str(end)))
        self.assert_exports(filters, [old.id], start.strftime('%d/%m/%Y'))

    def test_all_dates_restores_old_history_and_count_cache_keeps_modes_separate(self):
        for status in ['pending', 'resolved']:
            old = self.ticket(status=status)
            Ticket.objects.filter(pk=old.pk).update(created_at=timezone.now() - timedelta(days=100))
            self.ticket(status=status)
        filters = {'from': str(timezone.localdate() - timedelta(days=30)), 'to': str(timezone.localdate())}
        # Mismas fechas y usuario: cambiar solo el modo debe cambiar el conjunto.
        for mode, expected in [(None, (2, 1)), ('range', (1, 1)), ('all', (2, 2)), (None, (2, 1))]:
            params = {**filters, **({'date_mode': mode} if mode else {})}
            counts = self.client.get('/tickets/filter/', {**params, 'counts': 1, 'counts_only': 1}).json()['filtros']
            self.assertEqual((counts['pendientes'], counts['resueltos']), expected)
            for view, count in zip(['pendientes', 'resueltos'], expected):
                self.assertEqual(len(self.rows(view, **params)['tickets']), count)
        payload = self.rows('resueltos', date_mode='all', **filters)
        self.assertFalse(payload['applied_filters']['date_range_applied'])
        self.assert_exports({'view': 'resueltos', 'date_mode': 'all', **filters},
                            [t['id'] for t in payload['tickets']], 'Sin límite')

    def test_explicit_dates_preserve_customer_visibility(self):
        own = self.ticket()
        cc = self.ticket(requester=self.other)
        cc.ccs.add(self.customer)
        self.ticket(requester=self.other)
        self.client.force_login(self.customer)
        payload = self.rows('all_unsolved_no_tareas', date_mode='range')
        self.assertCountEqual([t['id'] for t in payload['tickets']], [own.id, cc.id])

    def test_all_queue_includes_unassigned_and_agents_without_assignment_rules(self):
        own = self.ticket()
        other = self.ticket(assignee=self.other)
        unassigned = self.ticket(assignee=None)
        payload = self.rows('all_unsolved_no_tareas', fast=1)
        self.assertCountEqual([t['id'] for t in payload['tickets']], [own.id, other.id, unassigned.id])
        self.assertEqual(payload['pagination']['total'], 3)
        self.assertEqual(next(t for t in payload['tickets'] if t['id'] == unassigned.id)['group_value'], 'Sin asignar')

    def test_on_hold_keeps_its_own_queue_and_is_also_visible_in_unsolved(self):
        hold = self.ticket(status='on_hold')
        self.ticket(status='resolved')
        self.ticket(status='closed')
        self.assertEqual([t['id'] for t in self.rows('espera')['tickets']], [hold.id])
        for view in ['no_resueltos', 'unsolved_no_tareas', 'home_all']:
            self.assertEqual([t['id'] for t in self.rows(view)['tickets']], [hold.id])
        counts = self.client.get('/tickets/filter/', {'counts': 1, 'counts_only': 1, 'force_counts': 1}).json()['filtros']
        self.assertEqual(counts['espera'], 1)
        self.assertEqual(counts['no_resueltos'], 1)

    def test_end_user_personal_queue_keeps_requester_and_cc_visibility(self):
        own = self.ticket(assignee=self.other)
        cc = self.ticket(requester=self.other)
        cc.ccs.add(self.customer)
        self.ticket(requester=self.other)
        self.client.force_login(self.customer)
        self.assertCountEqual([t['id'] for t in self.rows('unsolved_no_tareas')['tickets']], [own.id, cc.id])

    def test_home_personal_rating_counts_and_rows_only_use_assigned_tickets(self):
        mine = self.ticket(status='resolved')
        theirs = self.ticket(status='resolved', assignee=self.other, requester=self.agent)
        SatisfactionRating.objects.create(ticket=mine, requester=self.customer, score='good', created_at=timezone.now())
        SatisfactionRating.objects.create(ticket=theirs, requester=self.agent, score='bad', created_at=timezone.now())
        stats = self.client.get('/api/home-stats/').json()['stats']
        self.assertEqual((stats['bien'], stats['mal'], stats['total_rated'], stats['satisfaccion_pct']), (1, 0, 1, 100))
        self.assertEqual([t['id'] for t in self.rows('home_bien')['tickets']], [mine.id])
        self.assertEqual(self.rows('home_mal')['tickets'], [])
