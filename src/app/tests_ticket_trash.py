from datetime import timedelta

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from app.models import Role, Ticket, TicketEvent, User
from app.services.ticket_trash import TicketTrashService


@override_settings(ALLOWED_HOSTS=['testserver'], OUTBOUND_EMAIL_ENABLED=False,
                   AI_CLASSIFICATION_ENABLED=False)
class TicketTrashTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.agent = User.objects.create_user('trash-agent@example.test', 'Agente papelera')
        cls.agent.role = Role.objects.create(role_name='agent')
        cls.agent.save(update_fields=['role'])
        cls.customer = User.objects.create_user('trash-customer@example.test', 'Cliente')
        cls.customer.role = Role.objects.create(role_name='end user')
        cls.customer.save(update_fields=['role'])

    def setUp(self):
        cache.clear()
        self.ticket = Ticket.objects.create(subject='Papelera', description='Prueba', status='open',
                                            requester=self.customer, created_by=self.customer, assignee=self.agent)
        self.client.force_login(self.agent)

    def test_delete_and_restore_are_audited_and_idempotent(self):
        self.assertEqual(TicketTrashService.delete(self.agent, [self.ticket.id]), 1)
        self.ticket.refresh_from_db()
        deleted_at = self.ticket.deleted_at
        self.assertTrue(self.ticket.is_deleted)
        self.assertEqual(self.ticket.deleted_by, self.agent)
        self.assertIsNotNone(deleted_at)
        self.assertEqual(TicketTrashService.delete(self.agent, [self.ticket.id]), 0)
        self.ticket.refresh_from_db()
        self.assertEqual(self.ticket.deleted_at, deleted_at)
        self.assertEqual(TicketEvent.objects.filter(ticket=self.ticket, field_name='deleted').count(), 1)
        self.assertEqual(TicketTrashService.restore(self.agent, [self.ticket.id]), 1)
        self.assertEqual(TicketTrashService.restore(self.agent, [self.ticket.id]), 0)
        self.ticket.refresh_from_db()
        self.assertFalse(self.ticket.is_deleted)
        self.assertIsNone(self.ticket.deleted_at)
        self.assertIsNone(self.ticket.deleted_by_id)
        self.assertEqual(TicketEvent.objects.filter(ticket=self.ticket, field_name='restored', actor=self.agent).count(), 1)

    def test_trash_orders_by_deletion_and_falls_back_for_legacy_rows(self):
        legacy = Ticket.objects.create(subject='Borrado antiguo', description='Prueba', status='closed',
                                       requester=self.customer, created_by=self.customer, is_deleted=True)
        old = timezone.now() - timedelta(days=100)
        Ticket.objects.filter(pk=legacy.pk).update(created_at=old, updated_at=old)
        self.client.post('/tickets/bulk_delete/', {'ids': [self.ticket.id]}, content_type='application/json')
        payload = self.client.get('/tickets/filter/', {'view': 'trash', 'fast': 1}).json()
        self.assertEqual([t['id'] for t in payload['tickets']], [self.ticket.id, legacy.id])
        self.assertEqual(payload['tickets'][0]['deleted_by'], self.agent.name)
        self.assertIsNotNone(payload['tickets'][0]['deleted_at'])
        self.assertFalse(payload['applied_filters']['date_range_applied'])
        counts = self.client.get('/tickets/filter/', {'counts': 1, 'counts_only': 1, 'force_counts': 1}).json()['filtros']
        self.assertEqual(counts['trash'], 2)
        self.assertEqual(counts['unsolved_no_tareas'], 0)

    def test_only_agents_can_list_delete_restore_or_export_trash(self):
        self.client.force_login(self.customer)
        for path, query in [('/tickets/filter/', {'view': 'trash'}), ('/tickets/export.csv', {'view': 'trash'}),
                            ('/tickets/export.pdf', {'view': 'trash'})]:
            self.assertEqual(self.client.get(path, query).status_code, 403)
        for action in ['bulk_delete', 'bulk_restore']:
            self.assertEqual(self.client.post(f'/tickets/{action}/', {'ids': [self.ticket.id]},
                                             content_type='application/json').status_code, 403)
        self.ticket.refresh_from_db()
        self.assertFalse(self.ticket.is_deleted)

    def test_deleted_and_closed_tickets_reject_take_upload_and_ai(self):
        for values in [{'is_deleted': True}, {'is_deleted': False, 'status': 'closed'}]:
            Ticket.objects.filter(pk=self.ticket.pk).update(**values)
            for path in [f'/tickets/{self.ticket.pk}/take/', f'/tickets/{self.ticket.pk}/attachments/upload/',
                         f'/api/tickets/{self.ticket.pk}/ai-suggest-reply/']:
                with self.subTest(values=values, path=path):
                    response = self.client.post(path, {}, content_type='application/json')
                    self.assertEqual(response.status_code, 409)
                    self.assertEqual(response.json()['error']['code'], 'ticket_locked')

    def test_deleted_ticket_is_locked_and_hidden_from_profiles_and_requester_timeline(self):
        visible = Ticket.objects.create(subject='Visible', description='Prueba', status='open',
                                        requester=self.customer, created_by=self.customer)
        TicketTrashService.delete(self.agent, [self.ticket.id])
        response = self.client.get('/tickets/create/', {'id': self.ticket.id, 'fragment': 1})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['ticket_locked'])
        self.assertContains(response, 'Ticket eliminado')
        self.assertContains(response, 'data-restore-ticket=')
        self.assertNotContains(response, 'id="take-it-btn"')
        for path, query in [('/customers/profile/', {'id': self.customer.id}), ('/profile/', {})]:
            response = self.client.get(path, query)
            self.assertNotIn(self.ticket.id, [t.id for t in response.context['tickets']])
        response = self.client.get('/tickets/create/', {'id': visible.id})
        self.assertNotIn(self.ticket.id, [t['id'] for t in response.context['user_timeline']])

    def test_restore_returns_ticket_to_its_queue_and_reporting(self):
        TicketTrashService.delete(self.agent, [self.ticket.id])
        before = self.client.get('/reporting/data/', {'section': 'tickets'}).json()
        self.assertEqual(sum(r['n'] for r in before['charts']['dimensions']['product']['summary']), 0)
        response = self.client.post('/tickets/bulk_restore/', {'ids': [self.ticket.id]}, content_type='application/json')
        self.assertEqual(response.json()['restored'], 1)
        rows = self.client.get('/tickets/filter/', {'view': 'unsolved_no_tareas'}).json()['tickets']
        self.assertEqual([t['id'] for t in rows], [self.ticket.id])
        # Reporting conserva su caché habitual de 60 s. Aquí verificamos el
        # cálculo fresco, igual que hace la suite existente de reporting.
        cache.clear()
        after = self.client.get('/reporting/data/', {'section': 'tickets'}).json()
        self.assertEqual(sum(r['n'] for r in after['charts']['dimensions']['product']['summary']), 1)

    def test_invalid_requests_do_not_change_data(self):
        for payload in [[], {'ids': []}, {'ids': ['1']}, {'ids': [True]}, {'ids': [-1]}, {'ids': {}}]:
            for action in ['bulk_delete', 'bulk_restore']:
                with self.subTest(action=action, payload=payload):
                    self.assertEqual(self.client.post(f'/tickets/{action}/', payload,
                                                     content_type='application/json').status_code, 400)
        self.ticket.refresh_from_db()
        self.assertFalse(self.ticket.is_deleted)
        self.assertFalse(TicketEvent.objects.filter(ticket=self.ticket).exists())

    def test_merged_tickets_are_not_restored(self):
        source = Ticket.objects.create(subject='Fusionado', description='Prueba', status='closed',
                                       requester=self.customer, created_by=self.customer,
                                       merged_into=self.ticket, is_deleted=True)
        self.assertEqual(TicketTrashService.restore(self.agent, [source.id]), 0)
        source.refresh_from_db()
        self.assertTrue(source.is_deleted)
