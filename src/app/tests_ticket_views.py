import csv
import io
from datetime import timedelta

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
        # on_hold es el cierre de los agentes y cuenta como resuelto (#21384).
        for view in ['home_all', 'unsolved_no_tareas']:
            payload = self.rows(view)
            self.assertCountEqual([t['id'] for t in payload['tickets']], [old.id])
            self.assertNotIn(hold.id, [t['id'] for t in payload['tickets']])
            self.assertFalse(payload['applied_filters']['date_range_applied'])
        self.assertEqual(self.client.get('/api/home-stats/').json()['pendientes_count'], 1)

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

    def test_all_queue_includes_unassigned_and_agents_without_assignment_rules(self):
        own = self.ticket()
        other = self.ticket(assignee=self.other)
        unassigned = self.ticket(assignee=None)
        payload = self.rows('all_unsolved_no_tareas', fast=1)
        self.assertCountEqual([t['id'] for t in payload['tickets']], [own.id, other.id, unassigned.id])
        self.assertEqual(payload['pagination']['total'], 3)
        self.assertEqual(next(t for t in payload['tickets'] if t['id'] == unassigned.id)['group_value'], 'Sin asignar')

    def test_on_hold_counts_as_solved_and_has_its_own_queue_and_counter(self):
        hold = self.ticket(status='on_hold')
        self.ticket(status='resolved')
        self.ticket(status='closed')
        self.assertEqual([t['id'] for t in self.rows('espera')['tickets']], [hold.id])
        self.assertEqual(self.rows('no_resueltos')['tickets'], [])
        counts = self.client.get('/tickets/filter/', {'counts': 1, 'counts_only': 1, 'force_counts': 1}).json()['filtros']
        self.assertEqual(counts['espera'], 1)
        self.assertEqual(counts['no_resueltos'], 0)

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
