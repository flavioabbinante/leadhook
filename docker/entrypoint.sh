#!/bin/sh
set -e

wait_for_migrations() {
  # The web container applies migrations; worker and beat must not run before that.
  until python manage.py migrate --check >/dev/null 2>&1; do
    echo "Waiting for database migrations..."
    sleep 3
  done
}

case "$1" in
  web)
    python manage.py migrate --noinput
    exec gunicorn leadhook.wsgi:application --bind 0.0.0.0:8000 --workers "${WEB_CONCURRENCY:-3}"
    ;;
  worker)
    wait_for_migrations
    exec celery -A leadhook worker --loglevel "${LOG_LEVEL:-info}"
    ;;
  beat)
    wait_for_migrations
    exec celery -A leadhook beat --loglevel "${LOG_LEVEL:-info}" --schedule /tmp/celerybeat-schedule
    ;;
  *)
    exec "$@"
    ;;
esac
