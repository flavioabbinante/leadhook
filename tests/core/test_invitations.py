from datetime import timedelta

from django.conf import settings
from django.contrib.auth.models import Group, Permission, User
from django.contrib.messages import get_messages
from django.core import mail
from django.utils import timezone

from leadhook.core.models import Invitation

ADD = "/admin/core/invitation/add/"


def _invite(admin_client, **data) -> str:
    response = admin_client.post(ADD, {"email": "new@example.com", **data})
    assert response.status_code == 302
    message = " ".join(str(m) for m in get_messages(response.wsgi_request))
    return message.split("<code>")[1].split("</code>")[0]


def test_invitation_is_emailed_and_creates_a_staff_user(admin_client, client):
    group = Group.objects.create(name="Viewers")
    link = _invite(admin_client, groups=[group.pk])
    assert link in mail.outbox[0].body
    assert mail.outbox[0].to == ["new@example.com"]
    invitation = Invitation.objects.get()
    assert invitation.token_hash not in link  # only the hash is stored

    path = link.removeprefix(settings.PUBLIC_BASE_URL)
    assert "new@example.com" in client.get(path).content.decode()
    response = client.post(
        path, {"username": "maria", "password1": "a-long-pass-123", "password2": "a-long-pass-123"}
    )
    assert response.status_code == 302
    user = User.objects.get(username="maria")
    assert user.is_staff and not user.is_superuser
    assert user.email == "new@example.com"
    assert list(user.groups.all()) == [group]
    invitation.refresh_from_db()
    assert invitation.user == user and invitation.status == "Accepted"
    # Logged in straight away.
    assert client.get("/admin/").status_code == 200

    # Single use.
    assert client.post(path, {"username": "other"}).status_code == 410


def test_full_access_invitation_creates_superuser(admin_client, client):
    path = _invite(admin_client, is_superuser="on").removeprefix(settings.PUBLIC_BASE_URL)
    client.post(
        path, {"username": "boss", "password1": "a-long-pass-123", "password2": "a-long-pass-123"}
    )
    assert User.objects.get(username="boss").is_superuser


def test_expired_or_unknown_invitation_is_rejected(admin_client, client):
    path = _invite(admin_client).removeprefix(settings.PUBLIC_BASE_URL)
    Invitation.objects.update(expires_at=timezone.now() - timedelta(minutes=1))
    assert client.get(path).status_code == 410
    assert client.get("/invite/not-a-token/").status_code == 410


def test_only_superusers_can_invite(client):
    staff = User.objects.create_user("staff", password="x", is_staff=True)
    # Even holding every invitation permission is not enough without superuser status.
    staff.user_permissions.set(Permission.objects.filter(codename__endswith="invitation"))
    client.force_login(staff)
    assert client.get(ADD).status_code == 403
    assert client.post(ADD, {"email": "x@example.com", "is_superuser": "on"}).status_code == 403
    assert not Invitation.objects.exists()
