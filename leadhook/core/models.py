import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.utils import timezone

from .fields import EncryptedTextField


class Workspace(models.Model):
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


def get_default_workspace() -> Workspace:
    workspace = Workspace.objects.order_by("id").first()
    if workspace is None:
        workspace = Workspace.objects.create(name="Default")
    return workspace


INVITATION_LIFETIME = timedelta(days=7)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def default_expiry():
    return timezone.now() + INVITATION_LIFETIME


class Invitation(models.Model):
    """A single-use link that lets someone create their own admin account."""

    email = models.EmailField(help_text="Il link di invito viene inviato a questo indirizzo.")
    groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name="+",
        verbose_name="gruppi",
        help_text="Cosa può fare il nuovo utente. Ignorato con l'accesso completo.",
    )
    is_superuser = models.BooleanField(
        "Accesso completo",
        default=False,
        help_text="Tutti i permessi, compreso invitare altre persone.",
    )
    # Only the hash is stored: the link is shown once and emailed, never kept in clear.
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        related_name="+",
        verbose_name="creato da",
    )
    created_at = models.DateTimeField("creato il", auto_now_add=True)
    expires_at = models.DateTimeField("scade il", default=default_expiry)
    accepted_at = models.DateTimeField("accettato il", null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        verbose_name="utente",
        help_text="L'account creato da questo invito.",
    )

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "invito"
        verbose_name_plural = "inviti"

    def __str__(self) -> str:
        return self.email

    def issue_token(self) -> str:
        token = secrets.token_urlsafe(32)
        self.token_hash = hash_token(token)
        return token

    @property
    def is_pending(self) -> bool:
        return self.accepted_at is None and self.expires_at > timezone.now()

    @property
    def status(self) -> str:
        if self.accepted_at:
            return "Accettato"
        return "In attesa" if self.is_pending else "Scaduto"


class TwoFactorDevice(models.Model):
    """The TOTP authenticator of a user; deleting it makes them enroll again at next login."""

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="two_factor",
        verbose_name="utente",
    )
    secret = EncryptedTextField(editable=False)
    confirmed_at = models.DateTimeField("attivato il", null=True, blank=True)
    # Last accepted time step: a code is never accepted twice.
    last_counter = models.PositiveBigIntegerField(default=0, editable=False)

    class Meta:
        verbose_name = "verifica in due passaggi"
        verbose_name_plural = "verifiche in due passaggi"

    def __str__(self) -> str:
        return str(self.user)
