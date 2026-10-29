from django.test import Client, TestCase
from app.models import Notification, User


class ClearNotificationsTests(TestCase):
    def test_clears_all_own_notifications_and_preserves_other_users(self):
        user = User.objects.create_user('notify@example.test', 'Ana')
        other = User.objects.create_user('other@example.test', 'Bruno')
        Notification.objects.bulk_create([
            Notification(user=user, message=f'Aviso {i}') for i in range(125)
        ])
        peer = Notification.objects.create(user=other, message='Otro usuario')
        self.client.force_login(user)
        data = self.client.get('/api/notifications/').json()
        self.assertEqual(data['unread'], 125)
        self.assertEqual(len(data['notifications']), 20)
        self.assertEqual(self.client.get('/api/notifications/read-all/').status_code, 405)
        self.assertEqual(Client(enforce_csrf_checks=True).post('/api/notifications/read-all/').status_code, 403)
        response = self.client.post('/api/notifications/read-all/')
        self.assertEqual(response.json()['cleared'], 125)
        self.assertEqual(response.json()['unread'], 0)
        peer.refresh_from_db()
        self.assertFalse(peer.read)
        self.assertEqual(self.client.post('/api/notifications/read-all/').json()['cleared'], 0)
