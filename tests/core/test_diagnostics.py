from unittest import mock

import redis
from django.contrib.auth.models import User
from django.core.cache import cache
from django.urls import reverse

from leadhook.core.diagnostics import clear_cache

URL = reverse("diagnostics")


def _broker(**kwargs):
    client = mock.Mock(**kwargs)
    client.info.return_value = {"redis_version": "7.4", "used_memory_human": "1M"}
    client.llen.return_value = 3
    return client


def test_superuser_sees_checks_and_sidebar_link(admin_client):
    with mock.patch("leadhook.core.diagnostics._broker", return_value=_broker()):
        content = admin_client.get(URL).content.decode()
    assert "Raggiungibile" in content
    assert '<td><span class="lh-diag-dot lh-diag-info" aria-hidden="true"></span>3</td>' in content
    assert f'href="{URL}"' in content


def test_unreachable_redis_is_reported(admin_client):
    broken = mock.Mock()
    broken.info.side_effect = redis.ConnectionError("refused")
    with mock.patch("leadhook.core.diagnostics._broker", return_value=broken):
        content = admin_client.get(URL).content.decode()
    assert "Non raggiungibile: refused" in content


def test_staff_without_superuser_is_refused(client):
    user = User.objects.create_user("staff", password="pw", is_staff=True)
    client.force_login(user)
    assert client.get(URL).status_code == 403
    assert URL not in client.get(reverse("admin:index")).content.decode()


def test_clear_cache_deletes_only_leadhook_keys():
    client = mock.Mock()
    client.scan_iter.return_value = iter([b"leadhook:1:heartbeat", b"leadhook:1:alert"])
    client.delete.return_value = 2
    with mock.patch("leadhook.core.diagnostics._redis_cache", return_value=client):
        assert clear_cache() == 2
    assert client.scan_iter.call_args.args[0] == cache.make_key("*")
    client.delete.assert_called_once_with(b"leadhook:1:heartbeat", b"leadhook:1:alert")
