import os
from unittest.mock import MagicMock, call, patch

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import RequestFactory, SimpleTestCase, override_settings

from app.context_processors import fragment_base
from TicketFlow.settings import _resolve_ticketflow_version


class StartWebCommandTests(SimpleTestCase):
    @patch("app.management.commands.start_web.os.execvp")
    @patch("app.management.commands.start_web.call_command")
    @patch("app.management.commands.start_web.connection")
    def test_migrates_and_collects_static_files_before_starting_gunicorn(
        self, database, management_call, execvp
    ):
        database.vendor = "sqlite"

        call_command(
            "start_web", workers=2, threads=1, timeout=30, bind="127.0.0.1:9000"
        )

        self.assertEqual(
            management_call.call_args_list,
            [
                call("migrate", interactive=False, verbosity=1),
                call("collectstatic", interactive=False, verbosity=1),
            ],
        )
        execvp.assert_called_once()
        executable, command = execvp.call_args.args
        self.assertEqual(executable, "gunicorn")
        self.assertEqual(command[:2], ["gunicorn", "TicketFlow.wsgi:application"])
        workers_index = command.index("--workers")
        self.assertEqual(command[workers_index + 1], "2")

    @patch("app.management.commands.start_web.os.execvp")
    @patch("app.management.commands.start_web.call_command")
    @patch("app.management.commands.start_web.connection")
    def test_mysql_migration_lock_is_released_when_migration_fails(
        self, database, management_call, execvp
    ):
        database.vendor = "mysql"
        acquire_cursor = MagicMock()
        acquire_cursor.fetchone.return_value = (1,)
        release_cursor = MagicMock()
        database.cursor.side_effect = [
            MagicMock(__enter__=MagicMock(return_value=acquire_cursor)),
            MagicMock(__enter__=MagicMock(return_value=release_cursor)),
        ]
        management_call.side_effect = RuntimeError("migration failed")

        with self.assertRaisesRegex(RuntimeError, "migration failed"):
            call_command("start_web")

        acquire_cursor.execute.assert_called_once_with(
            "SELECT GET_LOCK(%s, %s)",
            ["ticketflow:start_web:migrations", 300],
        )
        release_cursor.execute.assert_called_once_with(
            "SELECT RELEASE_LOCK(%s)", ["ticketflow:start_web:migrations"]
        )
        database.close.assert_called_once()
        execvp.assert_not_called()

    @patch("app.management.commands.start_web.os.execvp")
    @patch("app.management.commands.start_web.call_command")
    @patch("app.management.commands.start_web.connection")
    def test_startup_stops_when_mysql_migration_lock_is_unavailable(
        self, database, management_call, execvp
    ):
        database.vendor = "mysql"
        acquire_cursor = MagicMock()
        acquire_cursor.fetchone.return_value = (0,)
        database.cursor.return_value = MagicMock(
            __enter__=MagicMock(return_value=acquire_cursor)
        )

        with self.assertRaisesRegex(CommandError, "migration lock"):
            call_command("start_web")

        management_call.assert_not_called()
        database.close.assert_called_once()
        execvp.assert_not_called()


class StaticFilesConfigurationTests(SimpleTestCase):
    def test_modern_django_uses_whitenoise_static_storage(self):
        self.assertEqual(
            settings.STORAGES["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedStaticFilesStorage",
        )


class VersionContextTests(SimpleTestCase):
    @patch.dict(os.environ, {"ARTIFACT_VERSION": "2.3.1-dev"}, clear=True)
    def test_artifact_version_is_used_by_default(self):
        self.assertEqual(_resolve_ticketflow_version(), "2.3.1-dev")

    @patch.dict(
        os.environ,
        {"TICKETFLOW_VERSION": "custom", "ARTIFACT_VERSION": "2.3.1-dev"},
        clear=True,
    )
    def test_explicit_version_overrides_artifact_tag(self):
        self.assertEqual(_resolve_ticketflow_version(), "custom")

    @patch.dict(os.environ, {}, clear=True)
    def test_local_fallback_is_not_a_stale_release(self):
        self.assertEqual(_resolve_ticketflow_version(), "dev")

    @override_settings(TICKETFLOW_VERSION="2.3.1-dev")
    def test_deployment_version_is_exposed_to_templates(self):
        request = RequestFactory().get("/")

        context = fragment_base(request)

        self.assertEqual(context["app_version"], "2.3.1-dev")
