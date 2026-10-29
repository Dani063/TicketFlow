from django.test import Client, TestCase
from app.models import Group, Role, Ticket, User


class WorkspaceProfileTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.role = Role.objects.create(role_name='agent')
        cls.agent = User.objects.create_user('workspace@example.test', 'Ana')
        cls.agent.role = cls.role
        cls.agent.save(update_fields=['role'])
        cls.other = User.objects.create_user('workspace-other@example.test', 'Bruno')

    def setUp(self):
        self.client.force_login(self.agent)
        self.url = '/api/profile/preferences/'

    def test_save_is_personal_and_does_not_change_permissions(self):
        payload = {'name': 'Ana Soporte', 'phone': '+34 910000000', 'role': 'admin',
                   'preferences': {'theme': 'dark', 'density': 'compact', 'signature': 'Un saludo,\nAna'}}
        response = self.client.post(self.url, payload, content_type='application/json')
        self.assertEqual(response.status_code, 200)
        self.agent.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.agent.name, 'Ana Soporte')
        self.assertEqual(self.agent.role_id, self.role.pk)
        self.assertEqual(self.other.workspace_preferences, {})
        self.assertEqual(self.agent.workspace_preferences['signature'], 'Un saludo,\nAna')

    def test_invalid_values_do_not_modify_profile(self):
        for payload in ({'preferences': {'theme': 'invalid'}}, {'name': ''},
                        {'photo_url': 'javascript:alert(1)'}, {'photo_url': 'http://example.test/avatar.png'},
                        {'preferences': {'signature': 'a' * 2001}},
                        {'preferences': {'notification_sound': 'false'}}):
            with self.subTest(payload=payload):
                self.assertEqual(self.client.post(self.url, payload, content_type='application/json').status_code, 400)
        self.agent.refresh_from_db()
        self.assertEqual(self.agent.name, 'Ana')
        self.assertEqual(self.agent.workspace_preferences, {})

    def test_json_auth_method_and_csrf_validation(self):
        self.assertEqual(self.client.post(self.url, '{', content_type='application/json').status_code, 400)
        self.assertEqual(self.client.get(self.url).status_code, 405)
        self.assertEqual(Client().post(self.url, {}, content_type='application/json').status_code, 401)
        secure = Client(enforce_csrf_checks=True)
        secure.force_login(self.agent)
        self.assertEqual(secure.post(self.url, {}, content_type='application/json').status_code, 403)

    def test_activity_filters_are_scoped_to_current_agent(self):
        own = Ticket.objects.create(subject='En espera', description='Test', requester=self.other,
                                    created_by=self.agent, assignee=self.agent, status='on_hold')
        Ticket.objects.create(subject='Ajeno', description='Test', requester=self.other,
                              created_by=self.other, assignee=self.other, status='on_hold')
        response = self.client.get('/tickets/filter/', {'view': 'home_hold'})
        self.assertEqual([row['id'] for row in response.json()['tickets']], [own.pk])
        Ticket.objects.filter(pk=own.pk).update(status='pending')
        response = self.client.get('/tickets/filter/', {'view': 'home_pending'})
        self.assertEqual([row['id'] for row in response.json()['tickets']], [own.pk])

    def test_profile_template_renders_preferences_and_existing_tickets(self):
        from django.urls import reverse
        response = self.client.get(reverse('profile'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'workspace-profile-form')
        self.assertContains(response, 'tickets-section')
        self.assertContains(response, 'Firma de correo')

    def test_own_customer_profile_redirects_to_settings_in_full_and_fragment_navigation(self):
        for user in (self.agent, self.other):
            self.client.force_login(user)
            for fragment in (False, True):
                response = self.client.get('/customers/profile/', {'id': user.pk, **({'fragment': '1'} if fragment else {})})
                self.assertEqual(response.status_code, 302)
                self.assertEqual(response.url, '/profile/' + ('?fragment=1' if fragment else ''))
                result = self.client.get(response.url)
                self.assertContains(result, 'workspace-profile profile-pane')

    def test_other_user_profile_shows_their_photo_and_not_my_settings(self):
        self.other.photo_url = '/media/profiles/other/avatar.jpg'
        self.other.save(update_fields=['photo_url'])
        response = self.client.get('/customers/profile/', {'id': self.other.pk})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'src="/media/profiles/other/avatar.jpg"')
        self.assertNotContains(response, 'workspace-profile-form')

    def test_photo_upload_is_validated_and_saved_as_a_small_image(self):
        import io
        import json
        import tempfile
        from PIL import Image
        from django.core.files.uploadedfile import SimpleUploadedFile
        from django.test import override_settings
        from django.core.files.storage import default_storage
        image = io.BytesIO()
        Image.new('RGB', (640, 480), 'purple').save(image, format='PNG')
        with tempfile.TemporaryDirectory() as directory, override_settings(MEDIA_ROOT=directory):
            response = self.client.post(self.url, {
                'payload': json.dumps({'preferences': {'signature': 'Un saludo'}}),
                'photo': SimpleUploadedFile('foto.png', image.getvalue(), content_type='image/png'),
            })
            self.assertEqual(response.status_code, 200)
            self.agent.refresh_from_db()
            self.assertIn(f'profiles/{self.agent.pk}/', self.agent.photo_url)
            import pathlib
            stored = next(pathlib.Path(directory).rglob('*.jpg'))
            with Image.open(stored) as saved:
                self.assertEqual(saved.size, (256, 256))
            response = self.client.post(self.url, {'payload': '{}', 'photo': SimpleUploadedFile('fake.png', b'not an image', content_type='image/png')})
            self.assertEqual(response.status_code, 400)
            # Guardar otros ajustes conserva la foto subida (su URL puede ser local).
            response = self.client.post(self.url, {'payload': json.dumps({'name': 'Ana'})})
            self.assertEqual(response.status_code, 200)
