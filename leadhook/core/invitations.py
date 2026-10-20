"""Invitations: a superuser invites someone by email, who then picks a username and password."""

from django.conf import settings
from django.contrib import admin
from django.contrib.auth import login
from django.contrib.auth.forms import UserCreationForm
from django.core.mail import send_mail
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.cache import never_cache

from .models import Invitation, hash_token


def invitation_link(token: str) -> str:
    return settings.PUBLIC_BASE_URL + reverse("accept-invitation", args=[token])


def send_invitation(invitation: Invitation, link: str) -> None:
    send_mail(
        "Sei stato invitato su LeadHook",
        f"Hai ricevuto un invito a LeadHook.\n\nCrea il tuo account qui: {link}\n\n"
        f"Il link si può usare una sola volta e scade il {invitation.expires_at:%d/%m/%Y}.",
        None,
        [invitation.email],
    )


@never_cache
def accept_invitation(request: HttpRequest, token: str) -> HttpResponse:
    invitation = Invitation.objects.filter(token_hash=hash_token(token)).first()
    context = {**admin.site.each_context(request), "title": "Crea il tuo account"}
    if invitation is None or not invitation.is_pending:
        return render(request, "admin/leadhook_invitation.html", context, status=410)
    form = UserCreationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            # Lock the row so two submissions of the same link cannot both create a user.
            invitation = Invitation.objects.select_for_update().get(pk=invitation.pk)
            if not invitation.is_pending:
                return render(request, "admin/leadhook_invitation.html", context, status=410)
            user = form.save(commit=False)
            user.email = invitation.email
            user.is_staff = True
            user.is_superuser = invitation.is_superuser
            user.save()
            user.groups.set(invitation.groups.all())
            invitation.accepted_at = timezone.now()
            invitation.user = user
            invitation.save(update_fields=["accepted_at", "user"])
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        return redirect("admin:index")
    context.update(form=form, invitation=invitation)
    return render(request, "admin/leadhook_invitation.html", context)
