#!/bin/sh
set -e

ROLE="${1:-web}"

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
    exec celery -A TicketFlow worker -l info --concurrency 2
    ;;
  beat)
    exec celery -A TicketFlow beat -l info -s /tmp/celerybeat-schedule
    ;;
  migrate)
    exec python main.py migrate --noinput
    ;;
  *)
    exec "$@"
    ;;
esac
