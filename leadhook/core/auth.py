from django.contrib.auth.backends import ModelBackend


class NoDeleteModelBackend(ModelBackend):
    """Only superusers may delete: "Can delete …" permissions given to other users or their
    groups are ignored, so a broad group can never wipe leads or settings."""

    def _get_permissions(self, user_obj, obj, from_name):
        perms = super()._get_permissions(user_obj, obj, from_name)
        if user_obj.is_superuser:
            return perms
        return {perm for perm in perms if not perm.partition(".")[2].startswith("delete_")}
