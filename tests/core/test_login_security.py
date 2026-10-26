import base64
import re
import time

import pytest
from django.contrib.auth.models import User
from django.core import mail
from django.test import Client

from leadhook.core.models import TwoFactorDevice
from leadhook.core.twofactor import SESSION_KEY, code_at, matching_counter

LOGIN = "/admin/login/"
TWO_FACTOR = "/admin/2fa/"


def _login(client, password="a-long-pass-123"):
    return client.post(LOGIN, {"username": "maria", "password": password, "next": "/admin/"})


def _user():
    return User.objects.create_user("maria", "maria@example.com", "a-long-pass-123", is_staff=True)


def test_totp_matches_rfc6238_vector():
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert code_at(secret, 1) == "287082"  # RFC 6238, T=59s, last 6 digits
    assert matching_counter(secret, "287082", now=59) == 1
    assert matching_counter(secret, "287082", now=59 + 30 * 5) is None


@pytest.mark.parametrize("code", ["", "28708", "2870820", "２８７０８２", " 287082"])
def test_totp_rejects_anything_but_six_ascii_digits(code):
    secret = base64.b32encode(b"12345678901234567890").decode()
    assert matching_counter(secret, code, now=59) is None


def _verified_client(user):
    client = Client()
    client.post(LOGIN, {"username": user.username, "password": "a-long-pass-123"})
    client.get(TWO_FACTOR)
    device = TwoFactorDevice.objects.get(user=user)
    client.post(TWO_FACTOR, {"code": code_at(device.secret, int(time.time() // 30))})
    assert client.get("/admin/").status_code == 200
    return client


def test_non_ascii_code_is_a_counted_failure(client):
    user = _user()
    _login(client)
    for _ in range(5):
        assert client.post(TWO_FACTOR, {"code": "１２３４５６"}).status_code == 200
    device = TwoFactorDevice.objects.get(user=user)
    good = code_at(device.secret, int(time.time() // 30))
    assert "Troppi tentativi" in client.post(TWO_FACTOR, {"code": good}).content.decode()


def test_deleting_the_device_revokes_verified_sessions():
    user = _user()
    client = _verified_client(user)
    TwoFactorDevice.objects.filter(user=user).delete()
    assert client.get("/admin/").url.startswith(TWO_FACTOR)
    # A new device does not bring the old session back either.
    TwoFactorDevice.objects.create(user=user, secret="A" * 32, confirmed_at="2026-01-01T00:00Z")
    assert client.get("/admin/").url.startswith(TWO_FACTOR)


def test_successful_logins_do_not_count_towards_the_lockout(client):
    _user()
    for _ in range(8):
        assert _login(client).status_code == 302


def test_login_locks_after_five_wrong_passwords(client):
    _user()
    for _ in range(5):
        assert _login(client, "wrong").status_code == 200
    # Right password, but locked: the login page is shown again.
    assert _login(client).status_code == 200
    assert "_auth_user_id" not in client.session


def test_password_login_requires_totp_enrollment_then_code(client):
    user = _user()
    assert _login(client).status_code == 302
    response = client.get("/admin/")
    assert response.status_code == 302 and response.url.startswith(TWO_FACTOR)

    page = client.get(TWO_FACTOR).content.decode()
    device = TwoFactorDevice.objects.get(user=user)
    assert device.secret[:4] in page and device.confirmed_at is None

    assert "Codice non valido" in client.post(TWO_FACTOR, {"code": "000000"}).content.decode()
    code = code_at(device.secret, int(time.time() // 30))
    assert client.post(TWO_FACTOR + "?next=/admin/", {"code": code}).url == "/admin/"
    assert client.get("/admin/").status_code == 200
    device.refresh_from_db()
    assert device.confirmed_at is not None


def test_totp_code_cannot_be_replayed(client):
    user = _user()
    _login(client)
    client.get(TWO_FACTOR)
    device = TwoFactorDevice.objects.get(user=user)
    code = code_at(device.secret, int(time.time() // 30))
    assert client.post(TWO_FACTOR, {"code": code}).status_code == 302

    other = client.__class__()
    other.post(LOGIN, {"username": "maria", "password": "a-long-pass-123"})
    assert other.post(TWO_FACTOR, {"code": code}).status_code == 200
    assert SESSION_KEY not in other.session


def test_password_reset_emails_a_working_link_and_is_throttled(client):
    _user()
    for _ in range(7):
        response = client.post("/admin/password_reset/", {"email": "maria@example.com"})
        assert response.status_code == 302
    assert len(mail.outbox) == 5
    link = re.search(r"https://leadhook\.test(/admin/reset/\S+/)", mail.outbox[0].body).group(1)
    form = client.get(link, follow=True)
    assert form.status_code == 200
    response = client.post(
        form.redirect_chain[-1][0],
        {"new_password1": "another-pass-456", "new_password2": "another-pass-456"},
        follow=True,
    )
    assert User.objects.get().check_password("another-pass-456")
    assert 'href="/admin/login/"' in response.content.decode()


def test_password_reset_for_unknown_address_looks_the_same(client):
    response = client.post("/admin/password_reset/", {"email": "nobody@example.com"})
    assert response.status_code == 302 and response.url == "/admin/password_reset/done/"
    assert mail.outbox == []


def test_login_page_links_password_reset(client):
    assert "/admin/password_reset/" in client.get(LOGIN).content.decode()


def test_late_give_back_does_not_touch_the_next_window(monkeypatch):
    from leadhook.core import auth

    monkeypatch.setattr(auth.time, "time", lambda: 0)
    reservation = auth.take_attempt("k")
    monkeypatch.setattr(auth.time, "time", lambda: auth.WINDOW_SECONDS)
    for _ in range(auth.MAX_ATTEMPTS):
        assert auth.take_attempt("k") is not None
    auth.give_back_attempt(reservation)  # the old request finishes in the new window
    assert auth.take_attempt("k") is None


def test_password_reset_smtp_error_reaches_the_task_retry(monkeypatch):
    from leadhook.core import tasks

    _user()
    monkeypatch.setattr(tasks, "send_mail", lambda *a, **k: (_ for _ in ()).throw(OSError()))
    with pytest.raises(OSError):
        tasks.send_password_reset.run("maria@example.com")
