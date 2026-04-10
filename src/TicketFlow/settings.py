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

BASE_DIR = Path(__file__).resolve().parent.parent
# Base para referenciar carpetas fuera de src/ (como resources)
PROJECT_ROOT = BASE_DIR.parent

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
# SECRET_KEY obligatorio: si no está, levantamos error para no arrancar inseguro
SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    raise RuntimeError("SECRET_KEY no definido. Configúralo en .env")

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
# Database (desde .env)
DATABASES = {
    "default": {
        "ENGINE": os.getenv("DB_ENGINE", "django.db.backends.mysql"),
        "NAME": os.getenv("DB_NAME", "ticketflow"),
        "USER": os.getenv("DB_USER", "root"),
        "PASSWORD": os.getenv("DB_PASSWORD", ""),
        "HOST": os.getenv("DB_HOST", "127.0.0.1"),
        "PORT": os.getenv("DB_PORT", "3306"),
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
   
