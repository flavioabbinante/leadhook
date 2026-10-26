from django.contrib.auth.backends import ModelBackend
from django.core.cache import caches
from django.core.cache.backends.redis import RedisCache
from django.core.exceptions import PermissionDenied

MAX_ATTEMPTS = 5
LOCKOUT_SECONDS = 15 * 60

# One round trip, so parallel requests cannot all slip under the limit and the key always
# keeps a TTL. The window restarts on every attempt over the limit: locked means 15 minutes
# since the last try.
_TAKE = """
local n = redis.call('INCR', KEYS[1])
if n == 1 or n >= tonumber(ARGV[2]) or redis.call('TTL', KEYS[1]) < 0 then
  redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return n
"""
_GIVE_BACK = """
if redis.call('EXISTS', KEYS[1]) == 1 then return redis.call('DECR', KEYS[1]) end
return 0
"""


def _eval(script: str, key: str) -> int:
    cache = caches["default"]
    # Django's cache API has no atomic increment-with-expiry, so talk to Redis directly.
    client = cache._cache.get_client(write=True)
    return client.eval(script, 1, cache.make_key(key), LOCKOUT_SECONDS, MAX_ATTEMPTS)


def take_attempt(key: str) -> bool:
    """Reserve one attempt; False once MAX_ATTEMPTS were used within LOCKOUT_SECONDS."""
    key = f"lockout:{key}"
    cache = caches["default"]
    if isinstance(cache, RedisCache):
        return _eval(_TAKE, key) <= MAX_ATTEMPTS
    # shortcut: not atomic across processes; fine for LocMemCache (tests, single process).
    cache.add(key, 0, LOCKOUT_SECONDS)
    count = cache.incr(key)
    if count >= MAX_ATTEMPTS:
        cache.touch(key, LOCKOUT_SECONDS)
    return count <= MAX_ATTEMPTS


def give_back_attempt(key: str) -> None:
    """Undo this request's own reservation after a success, leaving other failures counted."""
    key = f"lockout:{key}"
    cache = caches["default"]
    if isinstance(cache, RedisCache):
        _eval(_GIVE_BACK, key)
    elif key in cache:
        cache.decr(key)


class NoDeleteModelBackend(ModelBackend):
    """Only superusers may delete: "Can delete …" permissions given to other users or their
    groups are ignored, so a broad group can never wipe leads or settings.

    Also locks a username for LOCKOUT_SECONDS after MAX_ATTEMPTS wrong passwords."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        # shortcut: keyed on username only, since REMOTE_ADDR is the reverse proxy here;
        # add a per-IP limit once the proxy's X-Forwarded-For can be trusted.
        key = f"login:{(username or '').lower()}"
        if not take_attempt(key):
            raise PermissionDenied
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is not None:
            give_back_attempt(key)
        return user

    def _get_permissions(self, user_obj, obj, from_name):
        perms = super()._get_permissions(user_obj, obj, from_name)
        if user_obj.is_superuser:
            return perms
        return {perm for perm in perms if not perm.partition(".")[2].startswith("delete_")}
