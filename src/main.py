#!/usr/bin/env python
"""
Command-line utility for administrative tasks.

# For more information about this file, visit
# https://docs.djangoproject.com/en/2.1/ref/django-admin/
"""

import os
import sys

if __name__ == '__main__':
    # Ya que main.py ahora está dentro de 'src', solo necesitamos agregar su
    # propio directorio al path (aunque Python suele hacerlo por defecto).
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

    os.environ.setdefault(
        'DJANGO_SETTINGS_MODULE',
        'TicketFlow.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)
