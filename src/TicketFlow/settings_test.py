"""Configuración aislada para la suite local de Django.

Mantiene las migraciones y los tests reales, pero evita conexiones a AWS/RDS
y el coste criptográfico de crear usuarios de prueba con contraseña.
"""

import os

from django.conf import global_settings

os.environ["USE_SQLITE"] = "1"
os.environ["DISABLE_AWS_SSM"] = "1"

from .settings import *  # noqa: F401,F403,E402


# Las contraseñas creadas en los tests se calculan rápido. Conservamos los
# hashers normales para que también puedan validarse hashes preexistentes.
# Este ajuste solo existe en settings_test.
PASSWORD_HASHERS = [
    "django.contrib.auth.hashers.MD5PasswordHasher",
    *global_settings.PASSWORD_HASHERS,
]
