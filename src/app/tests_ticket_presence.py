from datetime import timedelta
from unittest.mock import patch

from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from app.models import Role, Ticket, TicketPresence, User


@override_settings(ALLOWED_HOSTS=['testserver'], OUTBOUND_EMAIL_ENABLED=False)
class TicketPresenceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        role = Role.objects.create(role_name='agent')
        cls.agent = User.objects.create_user('presence-a@example.test', 'Ana')
        cls.other = User.objects.create_user('presence-b@example.test', 'Bruno')
        for user in (cls.agent, cls.other):
            user.role = role
            user.save(update_fields=['role'])
        cls.customer = User.objects.create_user('presence-c@example.test', 'Cliente')
        cls.customer.role = Role.objects.create(role_name='end user')
        cls.customer.save(update_fields=['role'])
        cls.ticket = Ticket.objects.create(subject='Presencia', description='Prueba', status='open',
                                           requester=cls.customer, created_by=cls.customer, assignee=cls.agent)

    def setUp(self):
        self.client.force_login(self.agent)
        self.peer_client = Client()
        self.peer_client.force_login(self.other)
        self.url = f'/api/tickets/{self.ticket.pk}/presence/'

    def beat(self, client=None, **data):
        response = (client or self.client).post(self.url, data, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        return response.json()['data']

    def test_two_clients_see_each_other_and_composing_across_cache_resets(self):
        self.assertEqual(self.beat()['others'], [])
        peer = self.beat(self.peer_client, composing=True)
        self.assertEqual(peer['others'], [{'id': self.agent.id, 'name': 'Ana', 'composing': False}])
        cache.clear()
        mine = self.beat()
        self.assertEqual(mine['others'], [{'id': self.other.id, 'name': 'Bruno', 'composing': True}])
        self.assertEqual(mine['ttl_seconds'], 45)

    def test_same_agent_updates_one_row_instead_of_duplicating_presence(self):
        self.beat(composing=True)
        self.beat(composing=False)
        self.assertEqual(TicketPresence.objects.filter(ticket=self.ticket, user=self.agent).count(), 1)
        self.assertFalse(TicketPresence.objects.get(ticket=self.ticket, user=self.agent).composing)

    def test_expired_rows_are_purged_without_a_background_task(self):
        now = timezone.now()
        TicketPresence.objects.create(ticket=self.ticket, user=self.other,
                                       last_seen=now - timedelta(seconds=45), composing=True)
        with patch('app.services.presence.timezone.now', return_value=now):
            self.assertEqual(self.beat()['others'], [])
        self.assertFalse(TicketPresence.objects.filter(user=self.other).exists())

    def test_presence_does_not_leak_between_tickets(self):
        other_ticket = Ticket.objects.create(subject='Otro', description='Prueba', status='open',
                                             requester=self.customer, created_by=self.customer)
        TicketPresence.objects.create(ticket=other_ticket, user=self.other, last_seen=timezone.now())
        self.assertEqual(self.beat()['others'], [])

    def test_non_agents_and_anonymous_users_cannot_publish_or_read_presence(self):
        self.client.force_login(self.customer)
        self.assertEqual(self.client.post(self.url, {}, content_type='application/json').status_code, 403)
        self.client.logout()
        self.assertEqual(self.client.post(self.url, {}, content_type='application/json').status_code, 401)
        self.assertFalse(TicketPresence.objects.exists())

    def test_demoted_or_disabled_agents_are_not_returned(self):
        self.beat(self.peer_client)
        User.objects.filter(pk=self.other.pk).update(role=self.customer.role)
        self.assertEqual(self.beat()['others'], [])
        User.objects.filter(pk=self.other.pk).update(role=self.agent.role, is_active=False)
        self.assertEqual(self.beat()['others'], [])

    def test_late_departure_does_not_erase_the_newer_heartbeat(self):
        now = timezone.now()
        with patch('app.services.presence.timezone.now', return_value=now):
            old = self.beat()['last_seen']
        with patch('app.services.presence.timezone.now', return_value=now + timedelta(seconds=2)):
            latest = self.beat()['last_seen']
        self.beat(action='leave', seen_at=old)
        self.assertTrue(TicketPresence.objects.filter(ticket=self.ticket, user=self.agent).exists())
        self.beat(action='leave', seen_at=latest)
        self.assertFalse(TicketPresence.objects.filter(ticket=self.ticket, user=self.agent).exists())

    def test_beacon_form_uses_the_normal_csrf_protection(self):
        client = Client(enforce_csrf_checks=True)
        client.force_login(self.agent)
        client.get('/tickets/create/', {'id': self.ticket.id})
        token = client.cookies['csrftoken'].value
        response = client.post(self.url, {}, content_type='application/json', HTTP_X_CSRFTOKEN=token)
        self.assertEqual(response.status_code, 200)
        seen = response.json()['data']['last_seen']
        self.assertEqual(client.post(self.url, {'action': 'leave', 'seen_at': seen}).status_code, 403)
        response = client.post(self.url, {'action': 'leave', 'seen_at': seen, 'csrfmiddlewaretoken': token})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(TicketPresence.objects.filter(user=self.agent).exists())

    def test_invalid_payloads_are_rejected_without_creating_rows(self):
        for data in [[], {'composing': 'true'}, {'composing': 1}, {'action': 'invalid'},
                     {'action': 'leave', 'seen_at': '2026-99-99T12:00:00Z'},
                     {'action': 'leave', 'seen_at': '2026-10-27T12:00:00'}]:
            with self.subTest(data=data):
                self.assertEqual(self.client.post(self.url, data, content_type='application/json').status_code, 400)
        self.assertFalse(TicketPresence.objects.exists())

    def test_get_is_not_a_heartbeat_and_readonly_tickets_can_still_show_viewers(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)
        Ticket.objects.filter(pk=self.ticket.pk).update(status='closed')
        self.assertEqual(self.beat()['others'], [])
