"""
Django settings for TicketFlow project.

Based on 'django-admin startproject' using Django 2.1.2.

For more information on this file, see
https://docs.djangoproject.com/en/2.1/topics/settings/

For the full list of settings and their values, see
https://docs.djangoproject.com/en/2.1/ref/settings/
"""

import os
import posixpath
from pathlib import Path
from dotenv import load_dotenv


def _ssm_client():
    import boto3
    return boto3.client("ssm", region_name=os.getenv("AWS_REGION", "eu-west-1"))


def _get_ssm_parameter(name):
    """Fetches a single SecureString parameter from AWS SSM Parameter Store."""
    result = _ssm_client().get_parameter(Name=name, WithDecryption=True)
    return result["Parameter"]["Value"]


def _get_ssm_db_config(prefix):
    """Fetches DB credentials from AWS SSM Parameter Store.

    Expects:
      {prefix}/RDSCredentials  — SecureString JSON: {"username": "...", "password": "..."}
      {prefix}/RDSEndpoint     — SecureString: hostname
    """
    import json
    p = prefix.rstrip("/")
    result = _ssm_client().get_parameters(
        Names=[f"{p}/RDSCredentials", f"{p}/RDSEndpoint"],
        WithDecryption=True,
    )
    by_name = {param["Name"]: param["Value"] for param in result["Parameters"]}
    creds = json.loads(by_name[f"{p}/RDSCredentials"])
    return {
        "USER":     creds["username"],
        "PASSWORD": creds["password"],
        "HOST":     by_name[f"{p}/RDSEndpoint"],
    }

BASE_DIR = Path(__file__).resolve().parent.parent

def find_project_root(current_path, marker=".env"):
    """Busca dinámicamente la raíz del proyecto hacia arriba."""
    path = Path(current_path).resolve()
    for parent in [path] + list(path.parents):
        if (parent / marker).exists():
            return parent
    # Fallback si no encuentra el marcador
    return path.parent.parent.parent

# Base para referenciar carpetas fuera de src/ de forma dinámica
PROJECT_ROOT = find_project_root(__file__)

# Buscamos el .env un nivel por encima (raíz del proyecto)
env_path = PROJECT_ROOT / ".env"
if env_path.exists():
    load_dotenv(env_path)


# BASE_DIR is now resolved by Path.

STATICFILES_DIRS = [
    PROJECT_ROOT / "resources" / "static",
]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'app.User'

AUTHENTICATION_BACKENDS = [
    'app.backends.EmailBackend',  # Backend personalizado
      # Backend por defecto de Django
]

# === Seguridad y entorno ===
# Prioridad: variable de entorno (local/dev) → SSM (producción)
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    _ssm_prefix_early = os.getenv("AWS_SSM_PREFIX", "").rstrip("/")
    _secret_key_path = f"{_ssm_prefix_early}/SecretKey"
    try:
        SECRET_KEY = _get_ssm_parameter(_secret_key_path)
    except Exception as exc:
        raise RuntimeError(
            f"SECRET_KEY no definido y no se pudo obtener de SSM ({_secret_key_path}). "
            f"Configúralo en .env o verifica permisos IAM. Causa: {exc}"
        ) from exc

# DEBUG por entorno
DEBUG = os.getenv("DEBUG", "False").lower() in ("1", "true", "yes", "on")

# Hosts permitidos (coma-separados en .env). Si vacío -> lista vacía.
_hosts = os.getenv("ALLOWED_HOSTS", "").strip()
ALLOWED_HOSTS = [h.strip() for h in _hosts.split(",") if h.strip()]

# (opcional) Orígenes de confianza para CSRF (coma-separados)
_csrf = os.getenv("CSRF_TRUSTED_ORIGINS", "").strip()
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf.split(",") if o.strip()]

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'simple': {
            'format': '{levelname} {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console_app': {
            'class': 'logging.StreamHandler',
            'formatter': 'simple',
        },
    },
    'loggers': {
        # Logger para tu aplicación
        'app.views': {
            'handlers': ['console_app'],
            'level': 'DEBUG',
            'propagate': False,
        },
        # Reducir la verbosidad de los loggers de Django
        'django': {
            'handlers': ['console_app'],
            'level': 'WARNING',
            'propagate': False,
        },
        'django.request': {
            'handlers': ['console_app'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}
   

# Application references
# https://docs.djangoproject.com/en/2.1/ref/settings/#std:setting-INSTALLED_APPS
INSTALLED_APPS = [
    'health.apps.HealthConfig',
    'app',
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
]

# Middleware framework
# https://docs.djangoproject.com/en/2.1/topics/http/middleware/
MIDDLEWARE = [
    'health.middleware.HealthProbeHostMiddleware',
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'TicketFlow.urls'

# Template configuration
# https://docs.djangoproject.com/en/2.1/topics/templates/
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [PROJECT_ROOT / "resources" / "templates"],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'TicketFlow.wsgi.application'
# Database — credenciales desde AWS SSM Parameter Store (prod/dev) o .env (local sin SSM)
_ssm_prefix = os.getenv("AWS_SSM_PREFIX")
if _ssm_prefix:
    _ssm = _get_ssm_db_config(_ssm_prefix)
    _db_user     = _ssm["USER"]
    _db_password = _ssm["PASSWORD"]
    _db_host     = os.getenv("DB_HOST") or _ssm["HOST"]  # DB_HOST en .env permite tunelar
else:
    _db_user     = os.getenv("DB_USER",     "root")
    _db_password = os.getenv("DB_PASSWORD", "")
    _db_host     = os.getenv("DB_HOST",     "127.0.0.1")

DATABASES = {
    "default": {
        "ENGINE":   os.getenv("DB_ENGINE", "django.db.backends.mysql"),
        "NAME":     os.getenv("DB_NAME",   "ticketflow"),
        "USER":     _db_user,
        "PASSWORD": _db_password,
        "HOST":     _db_host,
        "PORT":     os.getenv("DB_PORT",   "3306"),
        "OPTIONS": {
            "init_command": "SET sql_mode='STRICT_TRANS_TABLES'"
        },
    }
}

# Password validation
# https://docs.djangoproject.com/en/2.1/ref/settings/#auth-password-validators
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
# https://docs.djangoproject.com/en/2.1/topics/i18n/
LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_L10N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
# https://docs.djangoproject.com/en/2.1/howto/static-files/
STATIC_URL = '/static/'
STATIC_ROOT = PROJECT_ROOT / "staticfiles"
# Ficheros subidos (adjuntos)
MEDIA_URL = '/media/'

MEDIA_ROOT = PROJECT_ROOT / "resources" / "media"
# Configuracion de la URL de inicio de sesi�n
LOGIN_URL = '/login/'
DEFAULT_CHARSET = 'utf-8'

# Configuraci�n de la URL de redirecci�n despu�s del inicio de sesi�n
LOGIN_REDIRECT_URL = '/'
