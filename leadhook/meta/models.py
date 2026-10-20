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
        SYSTEM_USER = "system_user", "System User (non scade mai)"
        USER = "user", "Utente"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="meta_apps")
    name = models.CharField("nome", max_length=200)
    app_id = models.CharField("ID app", max_length=64)
    app_secret = EncryptedTextField("App secret")
    verify_token = models.CharField(
        "token di verifica", max_length=64, default=generate_verify_token
    )
    webhook_uuid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    access_token = EncryptedTextField("token di accesso", blank=True)
    token_type = models.CharField(
        "tipo di token", max_length=20, choices=TokenType.choices, default=TokenType.SYSTEM_USER
    )
    token_expires_at = models.DateTimeField("scadenza del token", null=True, blank=True)
    graph_version = models.CharField(
        "versione Graph API", max_length=16, default=default_graph_version
    )
    login_config_id = models.CharField(
        "ID configurazione di accesso",
        max_length=64,
        blank=True,
        help_text=(
            "Facebook Login for Business → Configurations. Scegli una configurazione con un "
            "token di accesso System User, così il collegamento non scade mai."
        ),
    )

    class Meta:
        verbose_name = "app Meta"
        verbose_name_plural = "app Meta"

    def __str__(self) -> str:
        return f"{self.name} ({self.app_id})"

    def webhook_url(self) -> str:
        return settings.PUBLIC_BASE_URL + reverse("meta-webhook", args=[self.webhook_uuid])

    @staticmethod
    def login_redirect_url() -> str:
        return settings.PUBLIC_BASE_URL + reverse("facebook-callback")


class Page(models.Model):
    meta_app = models.ForeignKey(
        MetaApp, on_delete=models.CASCADE, related_name="pages", verbose_name="app Meta"
    )
    page_id = models.CharField("ID pagina", max_length=64, unique=True)
    name = models.CharField("nome", max_length=255, blank=True)
    page_token = EncryptedTextField("token della pagina", blank=True)
    subscribed = models.BooleanField("iscritta", default=False)
    last_checked_at = models.DateTimeField("ultimo controllo", null=True, blank=True)
    last_lead_at = models.DateTimeField("ultimo lead", null=True, blank=True)
    error = models.TextField("errore", blank=True)
    silence_alerted_at = models.DateTimeField("avviso di silenzio inviato", null=True, blank=True)
    default_destinations = models.ManyToManyField(
        "destinations.Destination",
        blank=True,
        related_name="default_for_pages",
        verbose_name="destinazioni predefinite",
        help_text="Usate per i moduli di questa pagina che non hanno destinazioni proprie.",
    )

    class Meta:
        verbose_name = "pagina"
        verbose_name_plural = "pagine"

    def __str__(self) -> str:
        return f"{self.name or 'Pagina'} ({self.page_id})"


class Form(models.Model):
    page = models.ForeignKey(
        Page, on_delete=models.CASCADE, related_name="forms", verbose_name="pagina"
    )
    form_id = models.CharField("ID modulo", max_length=64, unique=True)
    name = models.CharField("nome", max_length=255, blank=True)
    locale = models.CharField("lingua", max_length=16, blank=True)
    status = models.CharField("stato", max_length=32, blank=True)
    last_synced_at = models.DateTimeField("ultima sincronizzazione", null=True, blank=True)
    last_reconciled_at = models.DateTimeField("ultima riconciliazione", null=True, blank=True)
    questions = models.JSONField(
        "domande",
        default=list,
        blank=True,
        help_text="Le domande del modulo come le elenca Meta (chiave, etichetta).",
    )
    destinations = models.ManyToManyField(
        "destinations.Destination",
        blank=True,
        related_name="forms",
        verbose_name="destinazioni",
        help_text=(
            "Se impostate, sostituiscono le destinazioni predefinite della pagina "
            "per questo modulo."
        ),
    )

    class Meta:
        verbose_name = "modulo"
        verbose_name_plural = "moduli"

    def __str__(self) -> str:
        return f"{self.name or 'Modulo'} ({self.form_id})"
