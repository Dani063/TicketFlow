from importlib import import_module
from types import SimpleNamespace

from django.apps import apps
from django.db import connection
from django.test import TestCase, override_settings

from app.models import ProductLine, Role, Ticket, User
from app.services.tickets import TicketService


@override_settings(ALLOWED_HOSTS=['testserver'], OUTBOUND_EMAIL_ENABLED=False)
class ProductCatalogTests(TestCase):
    def test_new_products_are_selectable_before_other_services(self):
        agent = User.objects.create_user(email='catalog@example.test', name='Agente')
        agent.role = Role.objects.create(role_name='agent')
        agent.save(update_fields=['role'])
        self.client.force_login(agent)
        response = self.client.get('/tickets/create/')
        self.assertEqual(response.status_code, 200)
        for code, name in [('cognitia', 'Cognitia'), ('rightfax', 'Rightfax')]:
            product = ProductLine.objects.get(code=code)
            self.assertTrue(product.active)
            self.assertContains(response, name)
            self.assertLess(product.sort_order, ProductLine.objects.get(code='otros-servicios').sort_order)
            self.assertEqual(TicketService.product_from_service(f' {name.upper()} '), product)

    def test_data_migration_is_idempotent_and_does_not_reclassify_tickets(self):
        requester = User.objects.create_user(email='old-product@example.test', name='Cliente')
        ticket = Ticket.objects.create(subject='Cognitia histórico', description='Prueba',
                                       requester=requester, created_by=requester, service='cognitia', status='open')
        migration = import_module('app.migrations.0070_productline_cognitia_rightfax')
        ids = list(ProductLine.objects.filter(code__in=['cognitia', 'rightfax']).values_list('id', flat=True))
        schema = SimpleNamespace(connection=connection)
        migration.seed_products(apps, schema)
        migration.seed_products(apps, schema)
        self.assertCountEqual(ids, ProductLine.objects.filter(code__in=['cognitia', 'rightfax']).values_list('id', flat=True))
        ticket.refresh_from_db()
        self.assertIsNone(ticket.product_line_id)
        migration.unseed_products(apps, schema)
        self.assertFalse(ProductLine.objects.filter(code__in=['cognitia', 'rightfax']).exists())
        self.assertTrue(ProductLine.objects.filter(code='recordia').exists())
