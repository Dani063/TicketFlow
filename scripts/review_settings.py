"""Isolated SQLite/media settings for Daniel's local 21377 review."""
from pathlib import Path

from TicketFlow.settings import *  # noqa: F403

_review_root = Path(__file__).resolve().parents[1] / '.review-local'
DATABASES = {'default': {
    'ENGINE': 'django.db.backends.sqlite3',
    'NAME': _review_root / 'db.sqlite3',
    'OPTIONS': {'timeout': 20},
}}
MEDIA_ROOT = _review_root / 'media'
SESSION_COOKIE_NAME = 'ticketflow_review_21377'
KUBERNETES_HEALTH = {'ENABLE_KUBERNETES': False}
