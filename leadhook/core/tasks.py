from celery import shared_task
from django.conf import settings
from django.core.mail import send_mail

from .health import record_heartbeat


# smtplib errors are OSError subclasses: retry a few times, then give up (logged by Celery).
@shared_task(autoretry_for=(OSError,), retry_backoff=60, max_retries=3)
def send_alert_email(subject: str, body: str) -> None:
    send_mail(subject, body, None, settings.ALERT_EMAILS)


@shared_task
def heartbeat() -> None:
    record_heartbeat()
