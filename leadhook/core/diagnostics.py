"""Diagnostics page for superusers: Redis, Celery, database, Meta and settings at a glance."""

from dataclasses import dataclass
from urllib.parse import urlsplit

import redis
from django.conf import settings
from django.contrib import admin, messages
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import connection
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.utils import timezone

import leadhook
from leadhook.celery import app as celery_app
from leadhook.destinations.models import Delivery
from leadhook.leads.models import Lead, WebhookEvent
from leadhook.meta.models import MetaApp, Page

from .health import HEARTBEAT_MAX_AGE, heartbeat_age

TIMEOUT = 2  # seconds for each Redis or worker check


@dataclass
class Row:
    label: str
    value: str
    state: str = "info"  # "ok" | "warn" | "bad" | "info"


def _cache_pattern() -> str:
    # Matches only keys written through Django's cache ("<prefix>:<version>:<key>").
    return cache.make_key("*")


def _broker() -> redis.Redis:
    return redis.Redis.from_url(
        settings.CELERY_BROKER_URL, socket_timeout=TIMEOUT, socket_connect_timeout=TIMEOUT
    )


def _redis_cache() -> redis.Redis | None:
    """The Redis client behind the cache, or None when the cache is not Redis."""
    backend = getattr(cache, "_cache", None)
    return backend.get_client(write=True) if hasattr(backend, "get_client") else None


def _where(url: str) -> str:
    # Never show the password of the URL.
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.hostname}:{parts.port or 6379}{parts.path}"


def redis_rows() -> list[Row]:
    rows = [Row("Indirizzo", _where(settings.CELERY_BROKER_URL))]
    try:
        client = _broker()
        info = client.info()
        rows += [
            Row("Connessione", "Raggiungibile", "ok"),
            Row("Versione", info.get("redis_version", "?")),
            Row("Memoria usata", info.get("used_memory_human", "?")),
            Row("Task in coda", str(client.llen("celery"))),
        ]
    except redis.RedisError as exc:
        rows.append(Row("Connessione", f"Non raggiungibile: {exc}", "bad"))
    cache_client = _redis_cache()
    if cache_client is None:
        rows.append(Row("Cache", "In memoria del processo, non in Redis", "warn"))
    else:
        try:
            keys = sum(1 for _ in cache_client.scan_iter(_cache_pattern(), count=500))
            rows.append(Row("Chiavi della cache LeadHook", str(keys)))
        except redis.RedisError as exc:
            rows.append(Row("Cache", f"Non raggiungibile: {exc}", "bad"))
    return rows


def celery_rows() -> list[Row]:
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        rows = [Row("Modalità", "Eager: i task girano nel server web, senza worker", "warn")]
    else:
        try:
            replies = celery_app.control.ping(timeout=TIMEOUT)
        except Exception as exc:
            replies, error = [], str(exc)
        else:
            error = ""
        workers = ", ".join(name for reply in replies for name in reply)
        state = "ok" if replies else "bad"
        rows = [Row("Worker attivi", workers or error or "Nessuno risponde", state)]
    try:
        age = heartbeat_age()
    except Exception as exc:
        rows.append(Row("Beat", f"Cache non raggiungibile: {exc}", "bad"))
    else:
        if age is None:
            rows.append(Row("Beat", "Mai visto", "bad"))
        else:
            fresh = age < HEARTBEAT_MAX_AGE
            rows.append(Row("Beat", f"Ultimo battito {int(age)} s fa", "ok" if fresh else "bad"))
    return rows


def database_rows() -> list[Row]:
    try:
        connection.ensure_connection()
    except Exception as exc:
        return [Row("Connessione", f"Non raggiungibile: {exc}", "bad")]
    pending = Delivery.objects.filter(status=Delivery.Status.PENDING).count()
    failed = Delivery.objects.filter(status=Delivery.Status.FAILED).count()
    waiting = WebhookEvent.objects.filter(signature_ok=True, processed_at__isnull=True).count()
    return [
        Row("Connessione", f"Raggiungibile ({connection.vendor})", "ok"),
        Row("Lead", str(Lead.objects.count())),
        Row("Consegne in attesa", str(pending), "warn" if pending else "ok"),
        Row("Consegne fallite", str(failed), "bad" if failed else "ok"),
        Row("Eventi webhook da elaborare", str(waiting), "warn" if waiting else "ok"),
    ]


def meta_rows() -> list[Row]:
    now = timezone.now()
    rows = []
    for meta_app in MetaApp.objects.all():
        if not meta_app.access_token:
            rows.append(Row(f"App {meta_app.app_id}", "Nessun token", "bad"))
        elif meta_app.token_expires_at is None:
            rows.append(Row(f"App {meta_app.app_id}", "Token senza scadenza", "ok"))
        else:
            days = (meta_app.token_expires_at - now).days
            state = "bad" if days < 0 else "warn" if days < 7 else "ok"
            when = timezone.localtime(meta_app.token_expires_at).strftime("%d/%m/%Y")
            rows.append(Row(f"App {meta_app.app_id}", f"Token valido fino al {when}", state))
    broken = Page.objects.exclude(error="").count()
    rows.append(Row("Pagine con errori", str(broken), "bad" if broken else "ok"))
    return rows or [Row("App Meta", "Nessuna", "warn")]


def settings_rows() -> list[Row]:
    return [
        Row("Versione LeadHook", leadhook.__version__),
        Row("DEBUG", str(settings.DEBUG), "warn" if settings.DEBUG else "ok"),
        Row("PUBLIC_BASE_URL", settings.PUBLIC_BASE_URL),
        Row("TIME_ZONE", settings.TIME_ZONE),
        Row("ALLOW_PRIVATE_DESTINATIONS", str(settings.ALLOW_PRIVATE_DESTINATIONS)),
        Row("Email", settings.EMAIL_BACKEND.rsplit(".", 1)[-1]),
    ]


def clear_cache() -> int:
    """Delete LeadHook's cache keys only: the Celery queue may live in the same Redis DB."""
    client = _redis_cache()
    if client is None:
        cache.clear()
        return 0
    keys = list(client.scan_iter(_cache_pattern(), count=500))
    return client.delete(*keys) if keys else 0


def diagnostics_view(request):
    if not request.user.is_superuser:
        raise PermissionDenied
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "clear_cache":
            deleted = clear_cache()
            messages.success(
                request,
                f"Cache svuotata: {deleted} chiavi eliminate. Il battito di beat e i limiti "
                "sugli avvisi ripartono da zero.",
            )
        elif action == "sync_forms":
            from leadhook.meta.tasks import sync_forms

            sync_forms()
            messages.success(request, "Form sincronizzati da Meta.")
        return redirect("diagnostics")
    sections = [
        ("Redis", "zap", redis_rows()),
        ("Celery", "send", celery_rows()),
        ("Database", "box", database_rows()),
        ("Meta", "flag", meta_rows()),
        ("Impostazioni", "list", settings_rows()),
    ]
    context = {**admin.site.each_context(request), "title": "Diagnostica", "sections": sections}
    return TemplateResponse(request, "admin/leadhook_diagnostics.html", context)
