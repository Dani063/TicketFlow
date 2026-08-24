import csv
from datetime import timedelta
from io import StringIO

from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Brand, Organization, Role, Ticket, User


class TicketListFilterExportTests(TestCase):
    def setUp(self):
        self.agent_role = Role.objects.create(role_name='agent')
        self.end_role = Role.objects.create(role_name='End user')
        self.agent = User.objects.create_user('agent-export@example.com', 'Agente Export', 'secret')
        self.agent.role = self.agent_role
        self.agent.save(update_fields=['role'])

        self.telefonica = Organization.objects.create(name='Telefonica')
        self.other_org = Organization.objects.create(name='Otra empresa')
        self.requester = self._user('telefonica@example.com', 'Cliente Telefonica', self.telefonica)
        self.other_requester = self._user('other@example.com', 'Otro cliente', self.other_org)
        self.brand_a = Brand.objects.create(name='Recordia', support_email='recordia@example.com')
        self.brand_b = Brand.objects.create(name='eComFax', support_email='ecomfax@example.com')

        self.target = self._ticket(
            'Ticket objetivo con acentos: grabacion', self.requester, self.brand_a,
            status='resolved', service='Recordia', type='incident', channel='email',
            category='Configuration', resolved_at=timezone.now(),
        )
        self.same_brand_wrong_view = self._ticket(
            'Misma marca, otra vista', self.other_requester, self.brand_a,
        )
        self.other_brand = self._ticket(
            'Telefonica de otra marca', self.requester, self.brand_b,
        )
        self.old_ticket = self._ticket(
            'Telefonica demasiado antiguo', self.requester, self.brand_a,
        )
        Ticket.objects.filter(pk=self.old_ticket.pk).update(
            created_at=timezone.now() - timedelta(days=45)
        )

    def _user(self, email, name, organization):
        user = User.objects.create_user(email, name, 'secret')
        user.role = self.end_role
        user.organization = organization
        user.save(update_fields=['role', 'organization'])
        return user

    def _ticket(self, subject, requester, brand, **overrides):
        values = {
            'subject': subject,
            'description': 'Descripcion',
            'status': 'open',
            'priority': 'normal',
            'requester': requester,
            'assignee': self.agent,
            'created_by': requester,
            'brand': brand,
            'service': 'Recordia',
            'type': 'task',
            'channel': 'web',
        }
        values.update(overrides)
        return Ticket.objects.create(**values)

    def _params(self):
        today = timezone.localdate()
        return {
            'view': 'telefonica_mes',
            'brand_id': str(self.brand_a.id),
            'from': str(today - timedelta(days=30)),
            'to': str(today),
        }

    def test_table_applies_company_date_and_view_to_rows_counts_and_total(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse('filter_tickets'), self._params())
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual([row['id'] for row in payload['tickets']], [self.target.id])
        self.assertEqual(payload['filtros'], {})
        self.assertEqual(payload['pagination']['total'], 1)
        self.assertEqual(payload['applied_filters']['brand_id'], self.brand_a.id)
        self.assertEqual(payload['applied_filters']['view'], 'telefonica_mes')

        count_response = self.client.get(
            reverse('filter_tickets'), {**self._params(), 'counts': '1', 'counts_only': '1'}
        )
        self.assertEqual(count_response.json()['filtros']['telefonica_mes'], 1)

    def test_csv_contains_filter_context_reference_columns_and_only_result_rows(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse('tickets_export_csv'), self._params())
        self.assertEqual(response.status_code, 200)
        self.assertIn('text/csv', response['Content-Type'])
        today = timezone.localdate()
        expected_name = f'Tickets_recordia_telefonica_ultimo_mes_{today - timedelta(days=30):%Y%m%d}-{today:%Y%m%d}.csv'
        self.assertIn(expected_name, response['Content-Disposition'])

        content = b''.join(response.streaming_content).decode('utf-8-sig')
        rows = list(csv.reader(StringIO(content)))
        self.assertIn(['Empresa', 'Recordia'], rows)
        self.assertIn(['Vista', 'Tickets Telefónica último mes'], rows)
        header = [
            'ID', 'Status', 'Service', 'Subject', 'Type', 'Channel',
            'Requester', 'Priority', 'Requested', 'Solved', 'Category',
        ]
        header_index = rows.index(header)
        data_rows = rows[header_index + 1:]
        self.assertEqual(len(data_rows), 1)
        self.assertEqual(data_rows[0][0], str(self.target.id))
        self.assertEqual(data_rows[0][3], self.target.subject)
        self.assertEqual(data_rows[0][10], 'Configuration')
        self.assertNotIn(self.same_brand_wrong_view.subject, content)
        self.assertNotIn(self.other_brand.subject, content)

    def test_pdf_uses_same_filters_and_descriptive_filename(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse('tickets_export_pdf'), self._params())
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'application/pdf')
        self.assertTrue(response.content.startswith(b'%PDF'))
        self.assertGreater(len(response.content), 1500)
        today = timezone.localdate()
        expected_name = f'Tickets_recordia_telefonica_ultimo_mes_{today - timedelta(days=30):%Y%m%d}-{today:%Y%m%d}.pdf'
        self.assertIn(expected_name, response['Content-Disposition'])

    def test_end_user_export_never_escapes_visible_ticket_scope(self):
        self.client.force_login(self.requester)
        today = timezone.localdate()
        response = self.client.get(reverse('tickets_export_csv'), {
            'view': 'mis_tickets',
            'from': str(today - timedelta(days=30)),
            'to': str(today),
        })
        content = b''.join(response.streaming_content).decode('utf-8-sig')
        self.assertIn(self.target.subject, content)
        self.assertIn(self.other_brand.subject, content)
        self.assertNotIn(self.same_brand_wrong_view.subject, content)

    def test_tickets_page_renders_reporting_filters_and_both_exports(self):
        self.client.force_login(self.agent)
        response = self.client.get(reverse('tickets_list'))
        self.assertContains(response, 'id="ticketsFrom"')
        self.assertContains(response, 'id="ticketsTo"')
        self.assertContains(response, 'id="ticketsExportCsv"')
        self.assertContains(response, 'id="ticketsExportPdf"')
