#!/bin/sh
set -e

ROLE="${1:-web}"

# Valida que la cola SQS existe antes de arrancar worker o beat.
# El pod NO tiene permiso para crear colas — si no existe, falla con mensaje claro.
check_sqs_queue() {
    python - <<EOF
import boto3, os, sys
queue_url = os.getenv('CELERY_SQS_QUEUE_URL', '')
if not queue_url:
    print('[entrypoint] ERROR: CELERY_SQS_QUEUE_URL no está definida.')
    sys.exit(1)
try:
    region = os.getenv('AWS_REGION', 'eu-west-1')
    boto3.client('sqs', region_name=region).get_queue_attributes(
        QueueUrl=queue_url,
        AttributeNames=['QueueArn'],
    )
    print(f'[entrypoint] SQS queue OK: {queue_url}')
except Exception as e:
    print(f'[entrypoint] ERROR: cola SQS no accesible: {queue_url}')
    print(f'[entrypoint] Crea la cola externamente antes de desplegar.')
    print(f'[entrypoint] Detalle: {e}')
    sys.exit(1)
EOF
}

case "$ROLE" in
  web)
    exec gunicorn TicketFlow.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers 3 \
      --timeout 60 \
      --access-logfile - \
      --error-logfile - \
      --logger-class TicketFlow.gunicorn_logging.JsonLogger
    ;;
  worker)
    check_sqs_queue
    exec celery -A TicketFlow worker -l info --concurrency 2
    ;;
  beat)
    check_sqs_queue
    exec celery -A TicketFlow beat -l info -s /tmp/celerybeat-schedule
    ;;
  migrate)
    exec python main.py migrate --noinput
    ;;
  *)
    exec "$@"
    ;;
esac
