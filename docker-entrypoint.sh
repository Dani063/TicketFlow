#!/bin/sh
set -e

ROLE="${1:-web}"

# Valida que la cola SQS existe antes de arrancar worker o beat.
# El pod NO tiene permiso para crear colas — si no existe, falla con mensaje claro.
check_sqs_queue() {
    QUEUE_NAME="${CELERY_SQS_QUEUE_NAME:-ticketflow-celery}"
    python - <<EOF
import boto3, os, sys
region = os.getenv('AWS_REGION', 'eu-west-1')
queue = os.getenv('CELERY_SQS_QUEUE_NAME', 'ticketflow-celery')
try:
    boto3.client('sqs', region_name=region).get_queue_url(QueueName=queue)
    print(f'[entrypoint] SQS queue "{queue}" found OK')
except Exception as e:
    print(f'[entrypoint] ERROR: SQS queue "{queue}" no encontrada en {region}.')
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
      --error-logfile -
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
