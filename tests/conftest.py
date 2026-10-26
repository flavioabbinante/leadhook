import ipaddress

import pytest
from django.core.cache import cache


@pytest.fixture(autouse=True)
def _enable_db_for_all_tests(db):
    """Every test may touch the database; keeps individual tests free of markers."""


@pytest.fixture(autouse=True)
def fake_dns(monkeypatch):
    """Deterministic DNS for the SSRF guard (unknown hosts public, IP literals as themselves)."""
    table: dict[str, str] = {}

    def resolve(host: str, port: int) -> list[str]:
        try:
            return [str(ipaddress.ip_address(host))]
        except ValueError:
            return [table.get(host, "93.184.216.34")]

    monkeypatch.setattr("leadhook.destinations.http.resolve_host", resolve)
    return table


@pytest.fixture(autouse=True)
def _clear_cache():
    """Alert throttles and the heartbeat live in the cache; start every test without them."""
    cache.clear()


@pytest.fixture(autouse=True)
def _two_factor_passed_on_force_login(monkeypatch):
    """force_login (and admin_client) stand for a finished login, second factor included."""
    from django.test import Client

    from leadhook.core.twofactor import SESSION_KEY

    original = Client.force_login

    def force_login(self, user, backend=None):
        original(self, user, backend)
        session = self.session
        session[SESSION_KEY] = user.pk
        session.save()

    monkeypatch.setattr(Client, "force_login", force_login)
