from django.db import models

from leadhook.meta.models import Form, MetaApp, Page


class WebhookEvent(models.Model):
    meta_app = models.ForeignKey(
        MetaApp,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
        verbose_name="app Meta",
    )
    received_at = models.DateTimeField("ricevuto il", auto_now_add=True, db_index=True)
    payload = models.JSONField("contenuto", default=dict)
    signature_ok = models.BooleanField("firma valida", default=False)
    processed_at = models.DateTimeField("elaborato il", null=True, blank=True)
    error = models.TextField("errore", blank=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "evento webhook"
        verbose_name_plural = "eventi webhook"

    def __str__(self) -> str:
        return f"Evento {self.pk} del {self.received_at:%d/%m/%Y %H:%M:%S}"


class Lead(models.Model):
    class Status(models.TextChoices):
        RECEIVED = "received", "Ricevuto"
        FETCHED = "fetched", "Letto da Meta"
        ROUTED = "routed", "Instradato"
        UNROUTED = "unrouted", "Non instradato"
        DELIVERED = "delivered", "Consegnato"
        PARTIALLY_FAILED = "partially_failed", "Parzialmente fallito"
        FAILED = "failed", "Fallito"

    class Source(models.TextChoices):
        WEBHOOK = "webhook", "Webhook"
        RECONCILE = "reconcile", "Recupero"
        MANUAL = "manual", "Manuale"

    leadgen_id = models.CharField("ID lead", max_length=64, unique=True)
    page = models.ForeignKey(
        Page,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
        verbose_name="pagina",
    )
    form = models.ForeignKey(
        Form,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="leads",
        verbose_name="modulo",
    )
    page_external_id = models.CharField("ID pagina Meta", max_length=64, blank=True)
    form_external_id = models.CharField("ID modulo Meta", max_length=64, blank=True)
    created_time = models.DateTimeField("inviato il", null=True, blank=True)
    ad_id = models.CharField("ID inserzione", max_length=64, blank=True)
    ad_name = models.CharField("inserzione", max_length=255, blank=True)
    adset_id = models.CharField("ID gruppo di inserzioni", max_length=64, blank=True)
    adset_name = models.CharField("gruppo di inserzioni", max_length=255, blank=True)
    campaign_id = models.CharField("ID campagna", max_length=64, blank=True)
    campaign_name = models.CharField("campagna", max_length=255, blank=True)
    is_organic = models.BooleanField("organico", default=False)
    field_data = models.JSONField("risposte originali", default=list, blank=True)
    data = models.JSONField("risposte", default=dict, blank=True)
    source = models.CharField(
        "origine", max_length=16, choices=Source.choices, default=Source.WEBHOOK
    )
    status = models.CharField(
        "stato", max_length=20, choices=Status.choices, default=Status.RECEIVED, db_index=True
    )
    error = models.TextField("errore", blank=True)
    # Set by the retention purge; field_data/data are empty from then on.
    purged_at = models.DateTimeField("dati cancellati il", null=True, blank=True)
    received_at = models.DateTimeField("ricevuto il", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("aggiornato il", auto_now=True)

    class Meta:
        ordering = ["-received_at"]
        verbose_name = "lead"
        verbose_name_plural = "lead"

    def __str__(self) -> str:
        return f"Lead {self.leadgen_id}"
