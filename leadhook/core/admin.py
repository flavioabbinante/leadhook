from django.contrib import admin, messages
from django.utils.html import format_html

from .invitations import invitation_link, send_invitation
from .models import Invitation, TwoFactorDevice


@admin.register(Invitation)
class InvitationAdmin(admin.ModelAdmin):
    list_display = ("email", "status", "is_superuser", "created_by", "expires_at", "user")
    fields = ("email", "is_superuser", "groups")
    filter_horizontal = ("groups",)

    # Inviting grants permissions, so only superusers may do it (no privilege escalation).
    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        # Sent invitations are read-only; delete one to revoke it.
        return False

    @admin.display(description="Stato")
    def status(self, obj):
        return obj.status

    def save_model(self, request, obj, form, change):
        obj.created_by = request.user
        token = obj.issue_token()
        super().save_model(request, obj, form, change)
        link = invitation_link(token)
        try:
            send_invitation(obj, link)
            sent = f"Inviato via email a {obj.email}."
        except OSError:
            sent = "Non è stato possibile inviare l'email: condividi tu il link."
        self.message_user(
            request,
            format_html(
                "{} Questo link viene mostrato una sola volta: <code>{}</code>", sent, link
            ),
            messages.WARNING,
        )


@admin.register(TwoFactorDevice)
class TwoFactorDeviceAdmin(admin.ModelAdmin):
    """Superusers reset a lost authenticator by deleting the device."""

    list_display = ("user", "confirmed_at")
    fields = ("user", "confirmed_at")
    readonly_fields = fields

    def has_module_permission(self, request):
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return request.user.is_superuser

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return request.user.is_superuser
