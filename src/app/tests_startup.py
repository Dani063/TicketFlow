from unittest.mock import patch

from django.conf import settings
from django.core.management import call_command
from django.test import SimpleTestCase


class StartWebCommandTests(SimpleTestCase):
    @patch("app.management.commands.start_web.os.execvp")
    @patch("app.management.commands.start_web.call_command")
    def test_collects_static_files_before_starting_gunicorn(self, collectstatic, execvp):
        call_command("start_web", workers=2, threads=1, timeout=30, bind="127.0.0.1:9000")

        collectstatic.assert_called_once_with("collectstatic", interactive=False, verbosity=1)
        execvp.assert_called_once()
        executable, command = execvp.call_args.args
        self.assertEqual(executable, "gunicorn")
        self.assertEqual(command[:2], ["gunicorn", "TicketFlow.wsgi:application"])
        workers_index = command.index("--workers")
        self.assertEqual(command[workers_index + 1], "2")


class StaticFilesConfigurationTests(SimpleTestCase):
    def test_modern_django_uses_whitenoise_static_storage(self):
        self.assertEqual(
            settings.STORAGES["staticfiles"]["BACKEND"],
            "whitenoise.storage.CompressedStaticFilesStorage",
        )
