from django.contrib.auth.backends import ModelBackend
from django.core.cache import cache
from django.core.exceptions import PermissionDenied

MAX_FAILURES = 5
LOCKOUT_SECONDS = 15 * 60


def is_locked(key: str) -> bool:
    return cache.get(f"lockout:{key}", 0) >= MAX_FAILURES


def record_failure(key: str) -> None:
    cache.add(f"lockout:{key}", 0, LOCKOUT_SECONDS)
    cache.incr(f"lockout:{key}")


def clear_failures(key: str) -> None:
    cache.delete(f"lockout:{key}")


class NoDeleteModelBackend(ModelBackend):
    """Only superusers may delete: "Can delete …" permissions given to other users or their
    groups are ignored, so a broad group can never wipe leads or settings.

    Also locks a username for LOCKOUT_SECONDS after MAX_FAILURES wrong passwords."""

    def authenticate(self, request, username=None, password=None, **kwargs):
        # shortcut: keyed on username only, since REMOTE_ADDR is the reverse proxy here.
        key = f"login:{(username or '').lower()}"
        if is_locked(key):
            raise PermissionDenied
        user = super().authenticate(request, username=username, password=password, **kwargs)
        if user is None:
            record_failure(key)
        else:
            clear_failures(key)
        return user

    def _get_permissions(self, user_obj, obj, from_name):
        perms = super()._get_permissions(user_obj, obj, from_name)
        if user_obj.is_superuser:
            return perms
        return {perm for perm in perms if not perm.partition(".")[2].startswith("delete_")}
