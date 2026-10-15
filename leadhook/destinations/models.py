from django.core.exceptions import ValidationError
from django.db import models

from leadhook.core.fields import EncryptedTextField
from leadhook.core.models import Workspace
from leadhook.leads.models import Lead


class Destination(models.Model):
    class Kind(models.TextChoices):
        WEBHOOK = "webhook", "Webhook"

    class Method(models.TextChoices):
        POST = "POST", "POST"
        PUT = "PUT", "PUT"

    class AuthType(models.TextChoices):
        NONE = "none", "None"
        HEADER = "header", "Custom header"
        BEARER = "bearer", "Bearer token"
        HMAC = "hmac", "HMAC signature"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="destinations")
    name = models.CharField(max_length=200)
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.WEBHOOK)
    url = models.URLField("URL", max_length=2000)
    method = models.CharField(max_length=8, choices=Method.choices, default=Method.POST)
    auth_type = models.CharField(max_length=16, choices=AuthType.choices, default=AuthType.NONE)
    auth_header_name = models.CharField(
        max_length=100, blank=True, help_text="Header name for 'Custom header' authentication."
    )
    secret = EncryptedTextField(
        blank=True,
        help_text="Header value, bearer token or HMAC key, depending on authentication.",
    )
    extra_headers = models.JSONField(
        default=dict,
        blank=True,
        help_text='Additional headers as a JSON object, e.g. {"X-Source": "leadhook"}.',
    )
    payload_template = models.TextField(
        blank=True,
        help_text="Jinja2 template producing JSON. Leave empty for the standard payload.",
    )
    success_condition = models.TextField(
        blank=True,
        help_text="Optional Jinja2 expression, e.g. response.success == true. Empty means any 2xx.",
    )
    timeout_s = models.PositiveIntegerField("Timeout (seconds)", default=15)
    active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name

    def clean(self) -> None:
        errors: dict[str, str] = {}
        if self.auth_type == self.AuthType.HEADER and not self.auth_header_name:
            errors["auth_header_name"] = "Required for custom header authentication."
        if self.auth_type != self.AuthType.NONE and not self.secret:
            errors["secret"] = "Required for the selected authentication."
        if not isinstance(self.extra_headers, dict):
            errors["extra_headers"] = "Must be a JSON object."
        if errors:
            raise ValidationError(errors)


class Delivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        SUCCESS = "success", "Success"
        FAILED = "failed", "Failed"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="deliveries")
    destination = models.ForeignKey(
        Destination, on_delete=models.CASCADE, related_name="deliveries"
    )
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True)
    # Lease held by the worker currently sending; a crashed worker's lease simply expires.
    locked_until = models.DateTimeField(null=True, blank=True)
    last_status_code = models.PositiveIntegerField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    request_body = models.TextField(blank=True)
    response_body = models.TextField(blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "deliveries"
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["lead", "destination"], name="unique_delivery_per_destination"
            )
        ]

    def __str__(self) -> str:
        return f"{self.lead} → {self.destination}"

    @property
    def public_id(self) -> str:
        return f"dlv_{self.pk}" if self.pk else "dlv_preview"
