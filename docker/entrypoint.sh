#!/bin/sh
set -e

case "$1" in
  web)
    python manage.py migrate --noinput
    exec gunicorn leadhook.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-3}"
    ;;
  worker)
    exec celery -A leadhook worker --loglevel "${LOG_LEVEL:-info}"
    ;;
  beat)
    exec celery -A leadhook beat --loglevel "${LOG_LEVEL:-info}" --schedule /tmp/celerybeat-schedule
    ;;
  *)
    exec "$@"
    ;;
esac
