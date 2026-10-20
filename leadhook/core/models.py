import hashlib
import secrets
from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group
from django.db import models
from django.utils import timezone


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

    email = models.EmailField(help_text="The invitation link is emailed here.")
    groups = models.ManyToManyField(
        Group,
        blank=True,
        related_name="+",
        help_text="What the new user may do. Ignored with full access.",
    )
    is_superuser = models.BooleanField(
        "Full access",
        default=False,
        help_text="Every permission, including inviting other people.",
    )
    # Only the hash is stored: the link is shown once and emailed, never kept in clear.
    token_hash = models.CharField(max_length=64, unique=True, editable=False)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, related_name="+"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(default=default_expiry)
    accepted_at = models.DateTimeField(null=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
        help_text="The account created from this invitation.",
    )

    class Meta:
        ordering = ["-created_at"]

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
            return "Accepted"
        return "Pending" if self.is_pending else "Expired"
