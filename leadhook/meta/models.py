import secrets
import uuid

from django.conf import settings
from django.db import models
from django.urls import reverse

from leadhook.core.fields import EncryptedTextField
from leadhook.core.models import Workspace


def generate_verify_token() -> str:
    return secrets.token_hex(16)


def default_graph_version() -> str:
    return settings.META_GRAPH_VERSION


class MetaApp(models.Model):
    class TokenType(models.TextChoices):
        SYSTEM_USER = "system_user", "System user (never expires)"
        USER = "user", "User"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="meta_apps")
    name = models.CharField(max_length=200)
    app_id = models.CharField("App ID", max_length=64)
    app_secret = EncryptedTextField()
    verify_token = models.CharField(max_length=64, default=generate_verify_token)
    webhook_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    access_token = EncryptedTextField(blank=True)
    token_type = models.CharField(
        max_length=20, choices=TokenType.choices, default=TokenType.SYSTEM_USER
    )
    token_expires_at = models.DateTimeField(null=True, blank=True)
    graph_version = models.CharField(max_length=16, default=default_graph_version)
    login_config_id = models.CharField(
        "Login configuration ID",
        max_length=64,
        blank=True,
        help_text=(
            "Facebook Login for Business → Configurations. Choose a configuration with a "
            "System-user access token so the connection never expires."
        ),
    )

    class Meta:
        verbose_name = "Meta app"

    def __str__(self) -> str:
        return f"{self.name} ({self.app_id})"

    def webhook_url(self) -> str:
        return settings.PUBLIC_BASE_URL + reverse("meta-webhook", args=[self.webhook_uuid])

    @staticmethod
    def login_redirect_url() -> str:
        return settings.PUBLIC_BASE_URL + reverse("facebook-callback")


class Page(models.Model):
    meta_app = models.ForeignKey(MetaApp, on_delete=models.CASCADE, related_name="pages")
    page_id = models.CharField("Page ID", max_length=64, unique=True)
    name = models.CharField(max_length=255, blank=True)
    page_token = EncryptedTextField(blank=True)
    subscribed = models.BooleanField(default=False)
    last_checked_at = models.DateTimeField(null=True, blank=True)
    last_lead_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)
    silence_alerted_at = models.DateTimeField(null=True, blank=True)
    default_destinations = models.ManyToManyField(
        "destinations.Destination",
        blank=True,
        related_name="default_for_pages",
        help_text="Used for forms of this page that have no destinations of their own.",
    )

    def __str__(self) -> str:
        return f"{self.name or 'Page'} ({self.page_id})"


class Form(models.Model):
    page = models.ForeignKey(Page, on_delete=models.CASCADE, related_name="forms")
    form_id = models.CharField("Form ID", max_length=64, unique=True)
    name = models.CharField(max_length=255, blank=True)
    locale = models.CharField(max_length=16, blank=True)
    status = models.CharField(max_length=32, blank=True)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    last_reconciled_at = models.DateTimeField(null=True, blank=True)
    questions = models.JSONField(
        default=list, blank=True, help_text="The form's questions as Meta lists them (key, label)."
    )
    destinations = models.ManyToManyField(
        "destinations.Destination",
        blank=True,
        related_name="forms",
        help_text="When set, these replace the page's default destinations for this form.",
    )

    def __str__(self) -> str:
        return f"{self.name or 'Form'} ({self.form_id})"
