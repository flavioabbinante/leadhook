from django.db import models

from leadhook.meta.models import Form, MetaApp, Page


class WebhookEvent(models.Model):
    meta_app = models.ForeignKey(
        MetaApp, on_delete=models.SET_NULL, null=True, blank=True, related_name="events"
    )
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    payload = models.JSONField(default=dict)
    signature_ok = models.BooleanField(default=False)
    processed_at = models.DateTimeField(null=True, blank=True)
    error = models.TextField(blank=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"Event {self.pk} at {self.received_at:%Y-%m-%d %H:%M:%S}"


class Lead(models.Model):
    class Status(models.TextChoices):
        RECEIVED = "received", "Received"
        FETCHED = "fetched", "Fetched"
        ROUTED = "routed", "Routed"
        UNROUTED = "unrouted", "Unrouted"
        DELIVERED = "delivered", "Delivered"
        PARTIALLY_FAILED = "partially_failed", "Partially failed"
        FAILED = "failed", "Failed"

    class Source(models.TextChoices):
        WEBHOOK = "webhook", "Webhook"
        RECONCILE = "reconcile", "Reconciliation"
        MANUAL = "manual", "Manual"

    leadgen_id = models.CharField("Lead ID", max_length=64, unique=True)
    page = models.ForeignKey(
        Page, on_delete=models.SET_NULL, null=True, blank=True, related_name="leads"
    )
    form = models.ForeignKey(
        Form, on_delete=models.SET_NULL, null=True, blank=True, related_name="leads"
    )
    page_external_id = models.CharField("Meta page ID", max_length=64, blank=True)
    form_external_id = models.CharField("Meta form ID", max_length=64, blank=True)
    created_time = models.DateTimeField(null=True, blank=True)
    ad_id = models.CharField(max_length=64, blank=True)
    ad_name = models.CharField(max_length=255, blank=True)
    adset_id = models.CharField(max_length=64, blank=True)
    adset_name = models.CharField(max_length=255, blank=True)
    campaign_id = models.CharField(max_length=64, blank=True)
    campaign_name = models.CharField(max_length=255, blank=True)
    is_organic = models.BooleanField(default=False)
    field_data = models.JSONField(default=list, blank=True)
    data = models.JSONField(default=dict, blank=True)
    source = models.CharField(max_length=16, choices=Source.choices, default=Source.WEBHOOK)
    status = models.CharField(
        max_length=20, choices=Status.choices, default=Status.RECEIVED, db_index=True
    )
    error = models.TextField(blank=True)
    # Set by the retention purge; field_data/data are empty from then on.
    purged_at = models.DateTimeField(null=True, blank=True)
    received_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-received_at"]

    def __str__(self) -> str:
        return f"Lead {self.leadgen_id}"
