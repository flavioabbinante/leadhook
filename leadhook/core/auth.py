import time

from django.contrib.auth.backends import ModelBackend
from django.core.cache import caches
from django.core.cache.backends.locmem import LocMemCache
from django.core.cache.backends.redis import RedisCache
from django.core.exceptions import ImproperlyConfigured, PermissionDenied

MAX_ATTEMPTS = 5
WINDOW_SECONDS = 15 * 60

# Fixed windows: each has its own key, so a late give-back never touches the next window's
# count, and a lock ends with its window however often an attacker keeps trying.
# The key outlives its window, so it cannot expire and be recreated while still in use.
_TAKE = """
local n = redis.call('INCR', KEYS[1])
if n == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return n
"""
_GIVE_BACK = """
if redis.call('EXISTS', KEYS[1]) == 1 then return redis.call('DECR', KEYS[1]) end
return 0
"""


def _window_key(key: str) -> str:
    return f"lockout:{key}:{int(time.time() // WINDOW_SECONDS)}"


def _counter(script: str, window_key: str) -> int:
    cache = caches["default"]
    if isinstance(cache, RedisCache):
        # One round trip, so parallel requests cannot all slip under the limit. Django's
        # cache API has no atomic increment-with-expiry, hence the client underneath.
        client = cache._cache.get_client(write=True)
        return client.eval(script, 1, cache.make_key(window_key), 2 * WINDOW_SECONDS)
    if isinstance(cache, LocMemCache):
        # shortcut: in-process only, for the test suite.
        if script is _TAKE:
            cache.add(window_key, 0, 2 * WINDOW_SECONDS)
            return cache.incr(window_key)
        return cache.decr(window_key) if window_key in cache else 0
    raise ImproperlyConfigured("Login attempt limits need the Redis cache backend.")


def take_attempt(key: str) -> str | None:
    """Reserve one attempt: returns the reservation to give back on success, or None once
    MAX_ATTEMPTS were used in the current WINDOW_SECONDS."""
    window_key = _window_key(key)
    return window_key if _counter(_TAKE, window_key) <= MAX_ATTEMPTS else None


def give_back_attempt(reservation: str) -> None:
    """Undo this request's own reservation after a success, leaving other failures counted."""
    _counter(_GIVE_BACK, reservation)


class NoDeleteModelBackend(ModelBackend):
    """Only superusers may delete: "Can delete …" permissions given to other users or their
    groups are ignored, so a broad group can never wipe leads or settings.

    Also allows MAX_ATTEMPTS wrong passwords per username every WINDOW_SECONDS."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        # shortcut: keyed on username only, since REMOTE_ADDR is the reverse proxy here;
        # add a per-IP limit once the proxy's X-Forwarded-For can be trusted.
        key = f"login:{(username or '').lower()}"
        reservation = take_attempt(key)
        if reservation is None:
            raise PermissionDenied
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is not None:
            give_back_attempt(reservation)
        return user

    def _get_permissions(self, user_obj, obj, from_name):
        perms = super()._get_permissions(user_obj, obj, from_name)
        if user_obj.is_superuser:
            return perms
        return {perm for perm in perms if not perm.partition(".")[2].startswith("delete_")}
