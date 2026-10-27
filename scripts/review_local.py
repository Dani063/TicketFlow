"""Run an isolated local review. Never starts Celery, contacts AWS or sends mail."""
import argparse
import os
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8')
sys.stderr.reconfigure(encoding='utf-8')

ROOT = Path(__file__).resolve().parents[1]
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('action', choices=('prepare', 'run', 'check', 'test', 'inspect', 'close-expired', 'reset-data'))
parser.add_argument('--port', type=int, default=8010)
parser.add_argument('--tests', nargs='+', default=['app'], metavar='MODULO')
parser.add_argument('--case', default='07', metavar='CODIGO')
args = parser.parse_args()

# Process settings take precedence over any .env copied into this checkout.
os.environ.update({
    'USE_SQLITE': '1', 'DISABLE_AWS_SSM': '1', 'AWS_SSM_PREFIX': '',
    'AWS_EC2_METADATA_DISABLED': 'true', 'ENVIRONMENT_NAME': 'local-review',
    'SHORT_ENV': 'LOCAL', 'LONG_ENV': 'Local', 'SECRET_KEY': 'local-21377-review-only',
    'DEBUG': 'true', 'ALLOWED_HOSTS': '127.0.0.1,localhost,testserver',
    'CSRF_TRUSTED_ORIGINS': f'http://127.0.0.1:{args.port},http://localhost:{args.port}',
    'CELERY_BROKER_URL': 'memory://', 'CELERY_SQS_QUEUE_URL': '',
    'CELERY_TASK_ALWAYS_EAGER': 'false', 'AI_CLASSIFICATION_ENABLED': 'false',
    'TICKET_ON_HOLD_CLOSE_HOURS': '72', 'INBOUND_SWEEP_ALL_FOLDERS': 'true',
    'OUTBOUND_EMAIL_ENABLED': 'false', 'OUTBOUND_AGENT_REPLY_ENABLED': 'false',
    'OUTBOUND_TICKET_CREATED_ENABLED': 'false', 'OUTBOUND_TICKET_ON_HOLD_ENABLED': 'false',
    'SATISFACTION_SURVEY_ENABLED': 'false', 'INBOUND_MARK_READ_ENABLED': 'false',
    'ATTACHMENT_STORAGE_BACKEND': 'local', 'PUBLIC_ATTACHMENTS_S3_BUCKET': '',
    'HELP_CONTENT_S3_BUCKET': '', 'ATTACHMENT_STORAGE_PREFIX': '',
    'AZURE_CLIENT_ID': '', 'AZURE_CLIENT_SECRET': '', 'AZURE_TENANT_ID': '',
    'OPENAI_API_KEY': '', 'DJANGO_SETTINGS_MODULE': 'review_settings',
})
os.chdir(ROOT)
(ROOT / '.review-local').mkdir(exist_ok=True)
sys.path.insert(0, str(ROOT / 'src'))

import django
django.setup()
from django.conf import settings
from django.core.management import call_command

expected = (ROOT / '.review-local' / 'db.sqlite3').resolve()
assert settings.DATABASES['default']['ENGINE'] == 'django.db.backends.sqlite3'
assert Path(settings.DATABASES['default']['NAME']).resolve() == expected
assert not settings.OUTBOUND_EMAIL_ENABLED and not settings.CELERY_TASK_ALWAYS_EAGER
assert not settings.AI_CLASSIFICATION_ENABLED and not os.environ['AWS_SSM_PREFIX']
print(f'Revisión local: {ROOT}', flush=True)
print(f'SQLite: {expected}; correos, AWS SSM e IA desactivados.', flush=True)

if args.action in ('prepare', 'reset-data'):
    call_command('migrate', interactive=False, verbosity=1)
    from review_seed import seed_review
    seed_review(ROOT, reset=args.action == 'reset-data', port=args.port)
elif args.action == 'run':
    if not expected.is_file():
        raise SystemExit('Ejecuta primero: review_local.py prepare')
    print(f'Acceso: http://127.0.0.1:{args.port}/dev-login/ (Ctrl+C para detener).', flush=True)
    call_command('runserver', f'127.0.0.1:{args.port}', use_reloader=False)
elif args.action == 'check':
    call_command('check')
    call_command('makemigrations', check=True, dry_run=True)
elif args.action == 'test':
    call_command('test', *args.tests, interactive=False, verbosity=1)
elif args.action == 'close-expired':
    from app.services.ticket_status import close_expired_on_hold
    print('Tickets cerrados en la SQLite local:', close_expired_on_hold())
elif args.action == 'inspect':
    import json
    from django.utils import timezone
    from app.models import Ticket
    index = json.loads((ROOT / '.review-local' / 'escenarios.json').read_text(encoding='utf-8'))
    scenario = next((row for row in index['scenarios'] if row['code'] == args.case), None)
    if scenario is None:
        raise SystemExit('Código de escenario desconocido: '+args.case)
    ticket = Ticket.objects.get(pk=scenario['id'])
    fields = ('created_at', 'updated_at', 'on_hold_at', 'resolved_at', 'closed_at',
              'resolution_due_at', 'first_response_due_at')
    data = {'id':ticket.id,'subject':ticket.subject,'status':ticket.status,'is_deleted':ticket.is_deleted,
            'sla_paused_seconds':ticket.sla_paused_seconds or 0}
    data.update({name:timezone.localtime(value).isoformat() if (value:=getattr(ticket,name)) else None for name in fields})
    data['comments'] = ticket.comment_set.count()
    data['outbound_logs'] = list(ticket.outbound_email_logs.values('template_key','status','result'))
    print(json.dumps(data,ensure_ascii=False,indent=2))
