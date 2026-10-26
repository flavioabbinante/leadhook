from urllib.parse import urlsplit

from celery import shared_task
from django.conf import settings
from django.contrib.auth.forms import PasswordResetForm
from django.core.mail import send_mail
from django.template import loader

from .health import record_heartbeat


# smtplib errors are OSError subclasses: retry a few times, then give up (logged by Celery).
@shared_task(autoretry_for=(OSError,), retry_backoff=60, max_retries=3)
def send_alert_email(subject: str, body: str) -> None:
    send_mail(subject, body, None, settings.ALERT_EMAILS)


@shared_task
def heartbeat() -> None:
    record_heartbeat()


class _PasswordResetForm(PasswordResetForm):
    def send_mail(
        self,
        subject_template_name,
        email_template_name,
        context,
        from_email,
        to_email,
        html_email_template_name=None,
    ):
        # Django's version logs and swallows SMTP errors; let them reach the task's retry.
        subject = "".join(loader.render_to_string(subject_template_name, context).splitlines())
        body = loader.render_to_string(email_template_name, context)
        send_mail(subject, body, from_email, [to_email])


# shortcut: a retry mails again every account sharing the address; fine while one address
# has one account (invitations), split into a task per user if that changes.
@shared_task(autoretry_for=(OSError,), retry_backoff=60, max_retries=3)
def send_password_reset(email: str) -> None:
    """Mails a reset link (built on PUBLIC_BASE_URL) to the active users with this address."""
    form = _PasswordResetForm({"email": email})
    if form.is_valid():
        url = urlsplit(settings.PUBLIC_BASE_URL)
        form.save(
            domain_override=url.netloc,
            use_https=url.scheme == "https",
            extra_email_context={"site_name": "LeadHook"},
        )
