import re

from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models

from leadhook.core.fields import EncryptedTextField
from leadhook.core.models import Workspace
from leadhook.leads.models import Lead


def _header_text_problem(value: str) -> str | None:
    """Why a header name or value cannot be sent over HTTP/1.1, or None if it can."""
    if "\r" in value or "\n" in value:
        return "non può contenere a capo"
    try:
        value.encode("latin-1")
    except UnicodeEncodeError:
        return "può contenere solo caratteri Latin-1"
    return None


TELEGRAM_API = "https://api.telegram.org"
TELEGRAM_TOKEN = re.compile(r"\d+:[A-Za-z0-9_-]+")
TELEGRAM_CHAT_ID = re.compile(r"-?\d+|@[A-Za-z0-9_]{5,}")


class Destination(models.Model):
    class Kind(models.TextChoices):
        WEBHOOK = "webhook", "Webhook"
        TELEGRAM = "telegram", "Messaggio Telegram"

    class Method(models.TextChoices):
        POST = "POST", "POST"
        PUT = "PUT", "PUT"

    class AuthType(models.TextChoices):
        NONE = "none", "Nessuna"
        HEADER = "header", "Header personalizzato"
        BEARER = "bearer", "Bearer token"
        HMAC = "hmac", "Firma HMAC"

    workspace = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="destinations")
    name = models.CharField("nome", max_length=200)
    kind = models.CharField("tipo", max_length=20, choices=Kind.choices, default=Kind.WEBHOOK)
    url = models.URLField("URL", max_length=2000, blank=True)
    method = models.CharField("metodo", max_length=8, choices=Method.choices, default=Method.POST)
    auth_type = models.CharField(
        "tipo di autenticazione", max_length=16, choices=AuthType.choices, default=AuthType.NONE
    )
    auth_header_name = models.CharField(
        "nome dell'header",
        max_length=100,
        blank=True,
        help_text="Nome dell'header per l'autenticazione «Header personalizzato».",
    )
    secret = EncryptedTextField(
        "chiave segreta",
        blank=True,
        help_text=(
            "Valore dell'header, bearer token o chiave HMAC, secondo l'autenticazione. "
            "Per Telegram: il token del bot ricevuto da @BotFather."
        ),
    )
    message_template = models.TextField(
        "messaggio",
        blank=True,
        help_text=(
            "Markdown: **grassetto**, _corsivo_, `codice`, [link](https://…), # titolo, "
            "- elenco. Clicca un campo qui sotto per inserirlo. Se vuoto invia tutte le risposte."
        ),
    )
    telegram_chat_id = models.CharField(
        "ID della chat Telegram",
        max_length=64,
        blank=True,
        help_text="La chat, il gruppo o il canale che riceve il messaggio, es. -1001234567890.",
    )
    extra_headers = models.JSONField(
        "header aggiuntivi",
        default=dict,
        blank=True,
        help_text='Header aggiuntivi come oggetto JSON, es. {"X-Source": "leadhook"}.',
    )
    payload_template = models.TextField(
        "template del payload",
        blank=True,
        help_text=(
            "Per esperti: un template Jinja2 che produce JSON. Se compilato, sostituisce i "
            "campi da inviare. Lasciali vuoti entrambi per il payload standard."
        ),
    )
    success_condition = models.TextField(
        "condizione di successo",
        blank=True,
        help_text=(
            "Espressione Jinja2 facoltativa, es. response.success == true. "
            "Se vuota, va bene qualsiasi 2xx."
        ),
    )
    timeout_s = models.PositiveIntegerField(
        "timeout (secondi)",
        default=15,
        validators=[MinValueValidator(1), MaxValueValidator(120)],
    )
    active = models.BooleanField("attiva", default=True)
    created_at = models.DateTimeField("creata il", auto_now_add=True)

    class Meta:
        verbose_name = "destinazione"
        verbose_name_plural = "destinazioni"

    def __str__(self) -> str:
        return self.name

    @property
    def is_telegram(self) -> bool:
        return self.kind == self.Kind.TELEGRAM

    def endpoint_url(self) -> str:
        if self.is_telegram:
            return f"{TELEGRAM_API}/bot{self.secret}/sendMessage"
        return self.url

    def clean(self) -> None:
        errors: dict[str, str] = {}
        if self.is_telegram:
            # The token goes in the URL path: only the BotFather format is accepted.
            if not TELEGRAM_TOKEN.fullmatch(self.secret or ""):
                errors["secret"] = (
                    "Incolla il token del bot ricevuto da @BotFather, es. 123456:ABC-def."
                )
            if not TELEGRAM_CHAT_ID.fullmatch(self.telegram_chat_id):
                errors["telegram_chat_id"] = (
                    "Un numero come 123456789 o -1001234567890, oppure @nome."
                )
            self.method = self.Method.POST
            self.auth_type = self.AuthType.NONE
            if errors:
                raise ValidationError(errors)
            return
        if not self.url:
            errors["url"] = "Obbligatorio per un webhook."
        if self.auth_type == self.AuthType.HEADER and not self.auth_header_name:
            errors["auth_header_name"] = (
                "Obbligatorio per l'autenticazione con header personalizzato."
            )
        if self.auth_type != self.AuthType.NONE and not self.secret:
            errors["secret"] = "Obbligatorio per l'autenticazione scelta."
        if not isinstance(self.extra_headers, dict):
            errors["extra_headers"] = "Deve essere un oggetto JSON."
        else:
            for name, value in self.extra_headers.items():
                if not str(name).strip():
                    errors["extra_headers"] = "I nomi degli header non possono essere vuoti."
                    break
                problem = _header_text_problem(str(name))
                if problem:
                    errors["extra_headers"] = f"Il nome dell'header {name!r} {problem}."
                    break
                problem = _header_text_problem(str(value))
                if problem:
                    errors["extra_headers"] = f"Il valore dell'header {name!r} {problem}."
                    break
        if self.auth_type == self.AuthType.HEADER and self.auth_header_name:
            problem = _header_text_problem(self.auth_header_name)
            if problem:
                errors["auth_header_name"] = f"Il nome dell'header {problem}."
        # Bearer tokens and custom header values are sent as header values; HMAC keys are not.
        if self.auth_type in (self.AuthType.BEARER, self.AuthType.HEADER) and self.secret:
            problem = _header_text_problem(self.secret)
            if problem:
                # Never echo the secret itself.
                errors["secret"] = (
                    f"La chiave segreta viene inviata come valore di un header e {problem}."
                )
        if errors:
            raise ValidationError(errors)


class FieldMapping(models.Model):
    """One field of a destination's payload and where its value comes from."""

    FIXED = "fixed"
    # source values: "fixed", "answer:<question key>" or "lead:<path in the standard payload>".
    LEAD_SOURCES = {
        "id": "ID del lead Meta",
        "created_time": "Data di creazione del lead",
        "form.name": "Nome del form",
        "page.name": "Nome della pagina",
        "campaign.name": "Nome della campagna",
        "adset.name": "Nome del gruppo di inserzioni",
        "ad.name": "Nome dell'inserzione",
    }

    class Transform(models.TextChoices):
        NONE = "", "Così com'è"
        FIRST_WORD = "first_word", "Prima parola (nome)"
        REST_WORDS = "rest_words", "Altre parole (cognome)"
        STRIP_PLUS39 = "strip_plus39", "Telefono senza +39"

    destination = models.ForeignKey(
        Destination,
        on_delete=models.CASCADE,
        verbose_name="destinazione",
        related_name="field_mappings",
    )
    target = models.CharField(
        "nome del campo",
        max_length=200,
        help_text="Come lo chiama il CRM. Il punto annida: fields.email",
    )
    source = models.CharField("valore", max_length=200)
    fixed_value = models.CharField(
        "valore fisso",
        max_length=500,
        blank=True,
        help_text="Solo per «Valore fisso». I numeri restano numeri.",
    )
    transform = models.CharField(
        "trasformazione", max_length=20, choices=Transform.choices, blank=True
    )

    class Meta:
        verbose_name = "campo"
        verbose_name_plural = "campi"
        ordering = ("id",)

    def __str__(self) -> str:
        return self.target

    def clean(self) -> None:
        if any(not part.strip() for part in self.target.split(".")):
            raise ValidationError({"target": "Scrivi un nome prima e dopo ogni punto."})


class Delivery(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "In attesa"
        SUCCESS = "success", "Riuscita"
        FAILED = "failed", "Fallita"

    lead = models.ForeignKey(Lead, on_delete=models.CASCADE, related_name="deliveries")
    destination = models.ForeignKey(
        Destination,
        on_delete=models.CASCADE,
        verbose_name="destinazione",
        related_name="deliveries",
    )
    status = models.CharField(
        "stato", max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    attempts = models.PositiveIntegerField("tentativi", default=0)
    # Value of `attempts` when the current retry cycle started (bumped by a manual resend).
    retry_base = models.PositiveIntegerField("base dei tentativi", default=0)
    next_retry_at = models.DateTimeField("prossimo tentativo", null=True, blank=True)
    # Lease held by the worker currently sending; a crashed worker's lease simply expires.
    locked_until = models.DateTimeField("bloccata fino a", null=True, blank=True)
    last_status_code = models.PositiveIntegerField("ultimo codice HTTP", null=True, blank=True)
    last_error = models.TextField("ultimo errore", blank=True)
    request_body = models.TextField("corpo della richiesta", blank=True)
    response_body = models.TextField("corpo della risposta", blank=True)
    delivered_at = models.DateTimeField("consegnata il", null=True, blank=True)
    created_at = models.DateTimeField("creata il", auto_now_add=True)
    updated_at = models.DateTimeField("aggiornata il", auto_now=True)

    class Meta:
        verbose_name = "consegna"
        verbose_name_plural = "consegne"
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
