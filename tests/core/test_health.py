import time

from django.core.cache import cache

from leadhook.core.health import HEARTBEAT_KEY, heartbeat_is_fresh
from leadhook.core.tasks import heartbeat


def test_health_is_503_without_heartbeat(client):
    response = client.get("/health/")
    assert response.status_code == 503
    assert response.json() == {"database": "ok", "redis": "ok", "beat": "stale"}


def test_health_is_200_after_heartbeat(client):
    heartbeat()
    response = client.get("/health/")
    assert response.status_code == 200
    assert response.json() == {"database": "ok", "redis": "ok", "beat": "ok"}


def test_old_heartbeat_is_stale(client):
    cache.set(HEARTBEAT_KEY, time.time() - 301)
    assert client.get("/health/").json()["beat"] == "stale"
    assert not heartbeat_is_fresh()


def test_cache_failure_is_reported(client, monkeypatch):
    def down():
        raise ConnectionError("redis down")

    monkeypatch.setattr("leadhook.core.views.heartbeat_age", down)
    response = client.get("/health/")
    assert response.status_code == 503
    assert response.json()["redis"] == "error"
