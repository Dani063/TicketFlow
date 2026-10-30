"""Ejecuta los tests de TicketFlow sin AWS y con la configuración rápida.

Uso desde la raíz:
    python scripts/run_tests.py app
    python scripts/run_tests.py app.tests_ticket_views --timing
    python scripts/run_tests.py app --timing --durations 20
"""

import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
os.environ["DJANGO_SETTINGS_MODULE"] = "TicketFlow.settings_test"

from django.core.management import execute_from_command_line  # noqa: E402


if __name__ == "__main__":
    labels_and_options = sys.argv[1:] or ["app"]
    execute_from_command_line([sys.argv[0], "test", *labels_and_options])
