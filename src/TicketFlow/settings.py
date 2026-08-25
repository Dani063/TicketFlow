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
import logging as _logging
from pathlib import Path
from dotenv import load_dotenv


def _ssm_client():
    import boto3
    return boto3.client("ssm", region_name=os.getenv("AWS_REGION", "eu-west-1"))


def _get_ssm_parameter(name):
    """Fetches a single SecureString parameter from AWS SSM Parameter Store."""
    result = _ssm_client().get_parameter(Name=name, WithDecryption=True)
    return result["Parameter"]["Value"]


def _get_ssm_json(path):
    """Fetches a JSON SecureString from SSM and returns it parsed as dict."""
    import json
    try:
        return json.loads(_get_ssm_parameter(path))
    except Exception as exc:
        _exc_str = str(exc)
        if 'AccessDenied' in _exc_str or 'AccessDeniedException' in _exc_str:
            raise RuntimeError(
                f"Sin permisos IAM para leer {path}. "
                f"Añade ssm:GetParameter al rol del pod. Causa: {exc}"
            ) from exc
        if 'ParameterNotFound' in _exc_str:
            raise RuntimeError(
                f"Parámetro SSM no encontrado: {path}. "
                f"Créalo en Parameter Store como SecureString JSON."
            ) from exc
        raise


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
    load_dotenv(env_path, override=True)
if os.getenv("DISABLE_AWS_SSM", "").lower() in ("1", "true", "yes", "on"):
    os.environ["AWS_SSM_PREFIX"] = ""

# BASE_DIR is now resolved by Path.

STATICFILES_DIRS = [
    PROJECT_ROOT / "resources" / "static",
]

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

AUTH_USER_MODEL = 'app.User'

# Versión visible del producto. Es la única fuente usada por las plantillas;
# puede sobrescribirse en despliegue sin duplicar el valor en el frontend.
TICKETFLOW_VERSION = os.getenv('TICKETFLOW_VERSION', '2.2.0')

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
        _exc_str = str(exc)
        if 'AccessDenied' in _exc_str or 'AccessDeniedException' in _exc_str:
            raise RuntimeError(
                f"Sin permisos IAM para leer {_secret_key_path}. "
                f"Añade ssm:GetParameter al rol del pod. Causa: {exc}"
            ) from exc
        if 'ParameterNotFound' in _exc_str:
            raise RuntimeError(
                f"Parámetro SSM no encontrado: {_secret_key_path}. "
                f"Créalo en Parameter Store o define SECRET_KEY en .env."
            ) from exc
        raise

# DEBUG por entorno
DEBUG = os.getenv("DEBUG", "False").lower() in ("1", "true", "yes", "on")

# Hosts permitidos (coma-separados en .env). Si vacío -> lista vacía.
_hosts = os.getenv("ALLOWED_HOSTS", "").strip()
ALLOWED_HOSTS = [h.strip() for h in _hosts.split(",") if h.strip()]

# (opcional) Orígenes de confianza para CSRF (coma-separados)
_csrf = os.getenv("CSRF_TRUSTED_ORIGINS", "").strip()
CSRF_TRUSTED_ORIGINS = [o.strip() for o in _csrf.split(",") if o.strip()]

# === Logging ===
# Un único handler sobre el logger *raíz*: así queda cubierto cualquier módulo
# que use `logging.getLogger(__name__)`. Antes solo estaban configurados
# 'app.views' y 'django', y como no había logger raíz todo lo que emitían
# `app/services/`, `app/tasks.py` y `health/` por debajo de WARNING se
# descartaba en silencio (y lo de WARNING arriba salía por el `lastResort` de
# Python, sin formato ni nivel).
#
# LOG_LEVEL controla el nivel del código propio y se fija por entorno desde el
# ConfigMap: DEBUG en desarrollo, INFO o WARNING en producción.
_NIVELES_VALIDOS = {'CRITICAL', 'ERROR', 'WARNING', 'INFO', 'DEBUG', 'NOTSET'}

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
if LOG_LEVEL not in _NIVELES_VALIDOS:
    LOG_LEVEL = "INFO"

DJANGO_LOG_LEVEL = os.getenv("DJANGO_LOG_LEVEL", "WARNING").upper()
if DJANGO_LOG_LEVEL not in _NIVELES_VALIDOS:
    DJANGO_LOG_LEVEL = "WARNING"

# JSON en el entorno desplegado (el colector filtra por el campo 'level');
# texto plano en local, que es lo legible. Se puede forzar con LOG_FORMAT.
LOG_FORMAT = os.getenv("LOG_FORMAT", "text" if DEBUG else "json").lower()
if LOG_FORMAT not in ('json', 'text'):
    LOG_FORMAT = 'json'

LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'json': {
            '()': 'TicketFlow.logging_formatters.JsonFormatter',
        },
        'text': {
            'format': '{asctime} {levelname:<8} {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stdout',
            'formatter': LOG_FORMAT,
        },
    },
    # Raíz: cubre el código propio y cualquier librería sin entrada explícita.
    'root': {
        'handlers': ['console'],
        'level': LOG_LEVEL,
    },
    # Los loggers de abajo no declaran handler a propósito: propagan al raíz y
    # usan el suyo. Aquí solo se ajusta el nivel para recortar ruido.
    'loggers': {
        # Django en INFO comenta cada request; se mantiene el nivel reducido
        # que ya tenía. Los errores siguen llegando.
        'django': {
            'level': DJANGO_LOG_LEVEL,
        },
        'django.request': {
            'level': 'ERROR',
        },
        # Clientes de AWS y HTTP: en INFO son extremadamente verbosos y estos
        # pods hablan con SSM, SQS y SES continuamente.
        'boto3': {'level': 'WARNING'},
        'botocore': {'level': 'WARNING'},
        's3transfer': {'level': 'WARNING'},
        'urllib3': {'level': 'WARNING'},
        'msal': {'level': 'WARNING'},
        'openai': {'level': 'WARNING'},
        'httpx': {'level': 'WARNING'},
        'httpcore': {'level': 'WARNING'},
        'kombu': {'level': 'WARNING'},
    },
}
   

# Application references
# https://docs.djangoproject.com/en/2.1/ref/settings/#std:setting-INSTALLED_APPS
INSTALLED_APPS = [
    'health.apps.HealthConfig',
    'app',
    'django_celery_beat',
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
    'whitenoise.middleware.WhiteNoiseMiddleware',
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
                'app.context_processors.fragment_base',
            ],
        },
    },
]

WSGI_APPLICATION = 'TicketFlow.wsgi.application'
# Database — credenciales desde AWS SSM Parameter Store (prod/dev) o .env (local sin SSM)
# DISABLE_AWS_SSM=1 (variable de entorno, no .env) permite ejecutar checks/tests
# sin sesión AWS: el .env se carga con override=True, así que es la única forma
# de desactivar SSM puntualmente desde fuera. (Documentada en docs/backend-architecture.md)
if os.getenv("DISABLE_AWS_SSM", os.getenv("AWS_SSM_DISABLE", "")).lower() in ("1", "true", "yes", "on"):
    _ssm_prefix = None
else:
    _ssm_prefix = os.getenv("AWS_SSM_PREFIX")


# Credenciales agrupadas desde SSM (una sola llamada por bloque)
_ssm_azure   = _get_ssm_json(f"{_ssm_prefix}/AzureCredentials")   if _ssm_prefix else {}
_ssm_zendesk = _get_ssm_json(f"{_ssm_prefix}/ZendeskCredentials") if _ssm_prefix else {}


if _ssm_prefix:
    _ssm = _get_ssm_db_config(_ssm_prefix)
    _db_user     = _ssm["USER"]
    _db_password = _ssm["PASSWORD"]
    _db_host     = os.getenv("DB_HOST") or _ssm["HOST"]  # DB_HOST en .env permite tunelar
else:
    _db_user     = os.getenv("DB_USER",     "root")
    _db_password = os.getenv("DB_PASSWORD", "")
    _db_host     = os.getenv("DB_HOST",     "127.0.0.1")

if os.getenv("USE_SQLITE", "").lower() in ("1", "true", "yes", "on"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
else:
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
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
MEDIA_URL = '/media/'
MEDIA_ROOT = PROJECT_ROOT / "resources" / "media"

SSO_LOGIN_API_URL = os.getenv("SSO_LOGIN_API_URL", "https://dev-login-api.agentia365.com")
SSO_LOGIN_UI_URL = os.getenv("SSO_LOGIN_UI_URL", "https://dev-login.recordia.net/")

# === Zendesk (solo comandos de importación histórica) ===
# Local: desde .env | Producción: desde SSM /Recordia/.../ZendeskCredentials
ZENDESK_SUBDOMAIN = os.getenv('ZENDESK_SUBDOMAIN') or _ssm_zendesk.get('ZENDESK_SUBDOMAIN', '')
ZENDESK_EMAIL     = os.getenv('ZENDESK_EMAIL')     or _ssm_zendesk.get('ZENDESK_EMAIL', '')
ZENDESK_API_TOKEN = os.getenv('ZENDESK_API_TOKEN') or _ssm_zendesk.get('ZENDESK_API_TOKEN', '')

# === Celery ===
# Producción: SQS (sin broker propio que mantener, usa IAM del pod)
# Local dev:  sobreescribir con CELERY_BROKER_URL=redis://localhost:6379/0 en .env
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'sqs://')
_sqs_queue_url = os.getenv('CELERY_SQS_QUEUE_URL', '')
if not _sqs_queue_url and _ssm_prefix:
    try:
        _sqs_queue_url = _get_ssm_parameter(f"{_ssm_prefix}/CelerySQSQueueUrl")
        os.environ['CELERY_SQS_QUEUE_URL'] = _sqs_queue_url
    except Exception as _sqs_exc:
        _sqs_err = str(_sqs_exc)
        if 'AccessDenied' in _sqs_err or 'AccessDeniedException' in _sqs_err:
            raise RuntimeError(
                f"Sin permisos IAM para leer {_ssm_prefix}/CelerySQSQueueUrl. "
                f"Añade ssm:GetParameter al rol del pod. Causa: {_sqs_exc}"
            ) from _sqs_exc
        if 'ParameterNotFound' in _sqs_err:
            raise RuntimeError(
                f"Parámetro SSM no encontrado: {_ssm_prefix}/CelerySQSQueueUrl. "
                f"Créalo en Parameter Store (tipo String)."
            ) from _sqs_exc
        raise
# Si el broker es SQS, CELERY_SQS_QUEUE_URL es obligatoria para evitar auto-creación
if CELERY_BROKER_URL == 'sqs://' and not _sqs_queue_url:
    raise RuntimeError(
        "CELERY_SQS_QUEUE_URL no está definida. "
        "En local añade CELERY_BROKER_URL=redis://localhost:6379/0 al .env. "
        "En producción crea el parámetro SSM {prefix}/CelerySQSQueueUrl."
    )
# Deriva el nombre de la cola desde la URL (soporta colas FIFO con sufijo .fifo)
_sqs_queue_name = _sqs_queue_url.rstrip('/').split('/')[-1] if _sqs_queue_url else 'celery'
CELERY_TASK_DEFAULT_QUEUE = _sqs_queue_name
CELERY_BROKER_TRANSPORT_OPTIONS = {
    'region': os.getenv('AWS_REGION', 'eu-west-1'),
    'visibility_timeout': 3600,
    # La clave debe coincidir con el nombre real de la cola (incluido .fifo si aplica)
    'predefined_queues': {
        _sqs_queue_name: {'url': _sqs_queue_url},
    } if _sqs_queue_url else {},
}
# Modo eager (solo dev): ejecuta las tareas .delay() en el mismo proceso, sin worker
# ni broker. Gated por env, apagado por defecto -> cero impacto en producción. Útil
# para probar en local el flujo que dispara Celery (p.ej. clasificación IA al crear).
CELERY_TASK_ALWAYS_EAGER = os.getenv('CELERY_TASK_ALWAYS_EAGER', '').lower() in ('1', 'true', 'yes', 'on')
CELERY_TASK_EAGER_PROPAGATES = CELERY_TASK_ALWAYS_EAGER

# Celery, por defecto, ARRANCA quitando los handlers del logger raíz y pone los
# suyos. Eso se llevaría por delante la configuración de LOGGING justo en los dos
# roles donde más hace falta (worker y beat), dejándolos sin JSON ni nivel.
CELERY_WORKER_HIJACK_ROOT_LOGGER = False
# Los resultados de tareas no se usan — se descartan para no necesitar result backend
CELERY_TASK_IGNORE_RESULT = True
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'cache+memory://')
CELERY_BEAT_SCHEDULE = {
    'poll-m365-mailboxes': {
        'task': 'app.tasks.poll_m365_mailboxes',
        'schedule': 120.0,
    },
    'auto-assign-unassigned-tickets': {
        'task': 'app.tasks.auto_assign_unassigned_tickets',
        'schedule': 300.0,  # Cada 5 minutos
    },
    'mark-sla-breaches': {
        'task': 'app.tasks.mark_sla_breaches',
        'schedule': 300.0,
    },
    'notify-sla-at-risk': {
        'task': 'app.tasks.notify_sla_at_risk',
        'schedule': 300.0,
    },
    'offer-satisfaction-surveys': {
        'task': 'app.tasks.offer_satisfaction_surveys',
        'schedule': 900.0,  # Cada 15 min; el retardo real lo fija SATISFACTION_SURVEY_DELAY_HOURS
    },
}

# === Azure AD (lectura buzones M365 + envío via Graph sendMail) ===
# Local: desde .env | Producción: desde SSM /Recordia/.../AzureCredentials
AZURE_TENANT_ID     = os.getenv('AZURE_TENANT_ID')     or _ssm_azure.get('AZURE_TENANT_ID', '')
AZURE_CLIENT_ID     = os.getenv('AZURE_CLIENT_ID')     or _ssm_azure.get('AZURE_CLIENT_ID', '')
AZURE_CLIENT_SECRET = os.getenv('AZURE_CLIENT_SECRET') or _ssm_azure.get('AZURE_CLIENT_SECRET', '')

# === Azure OpenAI (alternativa heredada — opcional) ===
# El proveedor de IA en producción es OpenAI directo (ver más abajo, {prefix}/OpenAiToken).
# Este bloque solo aplica si algún día se aprovisiona Azure OpenAI: SSM
# {prefix}/AzureOpenAICredentials (SecureString JSON con ENDPOINT/API_KEY/DEPLOYMENT/
# API_VERSION). Su AUSENCIA es lo esperado hoy → log a debug, no warning (no implica
# que la IA esté apagada: eso lo decide _AI_HAS_CREDS más abajo).
_ssm_aoai = {}
if _ssm_prefix:
    try:
        _ssm_aoai = _get_ssm_json(f"{_ssm_prefix}/AzureOpenAICredentials")
    except Exception as _aoai_exc:
        _logging.getLogger(__name__).debug(
            "AzureOpenAICredentials no presente en SSM (esperado: se usa OpenAI directo): %s", _aoai_exc
        )
AZURE_OPENAI_ENDPOINT    = os.getenv('AZURE_OPENAI_ENDPOINT')    or _ssm_aoai.get('AZURE_OPENAI_ENDPOINT', '')
AZURE_OPENAI_API_KEY     = os.getenv('AZURE_OPENAI_API_KEY')     or _ssm_aoai.get('AZURE_OPENAI_API_KEY', '')
AZURE_OPENAI_DEPLOYMENT  = os.getenv('AZURE_OPENAI_DEPLOYMENT')  or _ssm_aoai.get('AZURE_OPENAI_DEPLOYMENT', 'gpt-4o-mini')
AZURE_OPENAI_API_VERSION = os.getenv('AZURE_OPENAI_API_VERSION') or _ssm_aoai.get('AZURE_OPENAI_API_VERSION', '2024-10-21')

# === OpenAI directo (proveedor de IA en producción) ===
# Sin endpoint de Azure, ai.py usa el cliente OpenAI estándar (api.openai.com) con
# OPENAI_API_KEY; el modelo va en OPENAI_MODEL. El resto del código es idéntico.
#
# Precedencia de la clave:
#   1. OPENAI_API_KEY del entorno/.env  -> LOCAL: tu clave de desarrollo SIEMPRE manda.
#   2. SSM {prefix}/OpenAiToken          -> PRODUCCIÓN: SecureString SIMPLE (no JSON)
#      que dejó sistemas; el pod lo lee con el rol IAM.
#   3. OPENAI_API_KEY dentro del JSON AzureOpenAICredentials -> compatibilidad.
# SSM SOLO se consulta si no hay clave en el entorno: así el local queda desacoplado
# del rollout a prod (no arrastra el secreto de producción al proceso de desarrollo)
# y un fallo de permisos/parámetro nunca rompe el arranque (queda en debug).
OPENAI_MODEL = os.getenv('OPENAI_MODEL') or _ssm_aoai.get('OPENAI_MODEL', 'gpt-4o-mini')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
if not OPENAI_API_KEY and _ssm_prefix:
    try:
        OPENAI_API_KEY = _get_ssm_parameter(f"{_ssm_prefix}/OpenAiToken").strip()
    except Exception as _oai_exc:
        _logging.getLogger(__name__).debug("OpenAiToken no disponible en SSM: %s", _oai_exc)
if not OPENAI_API_KEY:
    OPENAI_API_KEY = _ssm_aoai.get('OPENAI_API_KEY', '')

# Embeddings (casos similares + asistencia de respuesta). Mismo proveedor que el chat.
AZURE_OPENAI_EMBEDDING_DEPLOYMENT = os.getenv('AZURE_OPENAI_EMBEDDING_DEPLOYMENT') or _ssm_aoai.get('AZURE_OPENAI_EMBEDDING_DEPLOYMENT', '')
OPENAI_EMBEDDING_MODEL = os.getenv('OPENAI_EMBEDDING_MODEL') or _ssm_aoai.get('OPENAI_EMBEDDING_MODEL', 'text-embedding-3-small')

# Flags de la clasificación IA. Por defecto solo se enciende si hay credenciales
# (Azure OpenAI u OpenAI directo), así los entornos sin keys no notan ningún cambio.
_AI_HAS_CREDS = bool((AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_API_KEY) or OPENAI_API_KEY)
if not _AI_HAS_CREDS:
    _logging.getLogger(__name__).info(
        "IA desactivada: sin credenciales. Define OPENAI_API_KEY en .env para local, "
        "o el parámetro SSM {prefix}/OpenAiToken en el despliegue."
    )
AI_CLASSIFICATION_ENABLED = os.getenv(
    'AI_CLASSIFICATION_ENABLED',
    'true' if _AI_HAS_CREDS else 'false'
).lower() in ('1', 'true', 'yes', 'on')
AI_AUTO_APPLY = os.getenv('AI_AUTO_APPLY', 'true').lower() in ('1', 'true', 'yes', 'on')
AI_AUTO_APPLY_CONFIDENCE = float(os.getenv('AI_AUTO_APPLY_CONFIDENCE', '0.7'))
# Asistencia de respuesta IA (borradores a partir del ticket + casos similares).
# Se enciende por defecto si hay credenciales, como la clasificación.
AI_REPLY_ASSIST_ENABLED = os.getenv(
    'AI_REPLY_ASSIST_ENABLED', 'true' if _AI_HAS_CREDS else 'false'
).lower() in ('1', 'true', 'yes', 'on')
AI_SIMILAR_TICKETS_K = int(os.getenv('AI_SIMILAR_TICKETS_K', '3'))
AI_MAX_BODY_CHARS = int(os.getenv('AI_MAX_BODY_CHARS', '6000'))
AI_REQUEST_TIMEOUT_SECONDS = int(os.getenv('AI_REQUEST_TIMEOUT_SECONDS', '30'))

# === Email saliente (confirmaciones al cliente) ===
# SES autentica con el rol IAM del pod (como SQS/SSM): sin credenciales SMTP.
# Kill switch global; en local conviene dejarlo a false (BBDD compartida).
OUTBOUND_EMAIL_ENABLED = os.getenv('OUTBOUND_EMAIL_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
AWS_SES_REGION = os.getenv('AWS_SES_REGION', 'eu-west-1')
SES_CONFIGURATION_SET = os.getenv('SES_CONFIGURATION_SET', '')  # opcional: tracking de bounces/quejas
# Base de los enlaces {{ticket_url}} en plantillas (sin barra final)
TICKETFLOW_PUBLIC_URL = os.getenv('TICKETFLOW_PUBLIC_URL', '')

# === Encuesta de satisfacción (CSAT) ===
# Kill switch propio, independiente de OUTBOUND_EMAIL_ENABLED: permite tener el
# email saliente activo sin empezar a encuestar. Por defecto APAGADO.
SATISFACTION_SURVEY_ENABLED = os.getenv('SATISFACTION_SURVEY_ENABLED', 'false').lower() in ('1', 'true', 'yes', 'on')
# Horas desde la resolución antes de preguntar (Zendesk usaba 24): da margen a que
# el cliente compruebe la solución y evita encuestar reaperturas inmediatas.
SATISFACTION_SURVEY_DELAY_HOURS = int(os.getenv('SATISFACTION_SURVEY_DELAY_HOURS', '24'))
# Validez del enlace de voto. Dentro de la ventana el cliente puede rectificar.
SATISFACTION_SURVEY_TTL_DAYS = int(os.getenv('SATISFACTION_SURVEY_TTL_DAYS', '30'))
# Tope de encuestas por vuelta del barrido: acota el daño de un arranque en frío.
SATISFACTION_SURVEY_BATCH = int(os.getenv('SATISFACTION_SURVEY_BATCH', '200'))

# Configuracion de la URL de inicio de sesión
LOGIN_URL = '/login/'
DEFAULT_CHARSET = 'utf-8'

# Configuraci�n de la URL de redirecci�n despu�s del inicio de sesi�n
LOGIN_REDIRECT_URL = '/'
