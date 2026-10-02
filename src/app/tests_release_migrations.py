"""Regresión de la promoción: actualizar desde los dos historiales desplegados."""

from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.loader import MigrationLoader
from django.test import SimpleTestCase, TransactionTestCase


class MigrateJobSmokeCheckTests(SimpleTestCase):
    previous = ("app", "0063_restore_media_only_help_articles")
    target = ("app", "0064_migrate_job_smoke_check")

    def setUp(self):
        self.loader = MigrationLoader(None)

    def test_smoke_check_is_empty_and_follows_the_previous_migration(self):
        migration = self.loader.get_migration(*self.target)

        self.assertEqual(migration.dependencies, [self.previous])
        self.assertEqual(migration.operations, [])
        # Sigue habiendo una sola hoja, y el smoke check forma parte de su historia.
        leaves = self.loader.graph.leaf_nodes("app")
        self.assertEqual(len(leaves), 1)
        self.assertIn(self.target, self.loader.graph.forwards_plan(leaves[0]))
        self.assertFalse(self.loader.detect_conflicts())

    def test_smoke_check_does_not_change_the_model_state(self):
        before = self.loader.project_state([self.previous])
        after = self.loader.project_state([self.target])

        self.assertEqual(after.models, before.models)


class ReleaseMigrationTests(TransactionTestCase):
    base = ("app", "0052_refresh_help_center_content")
    sta = ("app", "0053_seed_agent_reply_templates")
    dev = ("app", "0058_attachment_drafts_requests")
    merged = ("app", "0059_merge_sta_dev")

    def setUp(self):
        super().setUp()
        self.addCleanup(self.restore_latest_schema)
        MigrationExecutor(connection).migrate([self.base])

    def restore_latest_schema(self):
        executor = MigrationExecutor(connection)
        executor.migrate(executor.loader.graph.leaf_nodes())

    def assert_upgrade_preserves_templates(self, source, missing_branch):
        executor = MigrationExecutor(connection)
        executor.migrate([source])
        executor = MigrationExecutor(connection)
        executor.loader.check_consistent_history(connection)
        self.assertIn(source, executor.loader.applied_migrations)
        self.assertNotIn(missing_branch, executor.loader.applied_migrations)
        self.assertFalse(executor.loader.detect_conflicts())

        Template = executor.loader.project_state([source]).apps.get_model("app", "ResponseTemplate")
        templates = Template.objects.filter(key="agent_reply", brand__isnull=True)
        self.assertEqual(templates.count(), 2)
        templates.filter(language="es").update(
            subject="Plantilla personalizada existente", active=False,
        )
        before = list(templates.order_by("id").values())

        plan = executor.migration_plan([self.merged])
        self.assertTrue(plan)
        self.assertTrue(all(not backwards for _, backwards in plan))
        executor.migrate([self.merged])

        executor = MigrationExecutor(connection)
        executor.loader.check_consistent_history(connection)
        # Lo que importa es que el historial vuelva a tener una sola hoja: es la
        # prueba de que las dos ramas han convergido. No se fija en cual, porque
        # cada migracion posterior a la merge la desplaza; se comprueba en su
        # lugar que esa hoja desciende de la merge.
        leaves = executor.loader.graph.leaf_nodes("app")
        self.assertEqual(len(leaves), 1, f"historial bifurcado: {leaves}")
        self.assertIn(self.merged, executor.loader.graph.forwards_plan(leaves[0]))
        self.assertFalse(executor.migration_plan([self.merged]))
        Template = executor.loader.project_state([self.merged]).apps.get_model("app", "ResponseTemplate")
        after = list(Template.objects.filter(key="agent_reply", brand__isnull=True).order_by("id").values())
        self.assertEqual(after, before)
        self.assertTrue({"app_attachmentdraft", "app_attachmentrequest", "app_productline",
                         "app_helparticlerevision"}.issubset(connection.introspection.table_names()))

    def test_upgrade_from_sta_preserves_existing_templates(self):
        self.assert_upgrade_preserves_templates(self.sta, self.dev)

    def test_upgrade_from_dev_preserves_existing_templates(self):
        self.assert_upgrade_preserves_templates(self.dev, self.sta)
