from django.contrib import admin

from leadhook.core.admin_forms import SecretAwareModelForm
from leadhook.core.models import get_default_workspace

from .models import Form, MetaApp, Page


class MetaAppForm(SecretAwareModelForm):
    secret_fields = ("app_secret", "access_token")

    class Meta:
        model = MetaApp
        exclude = ("workspace",)


@admin.register(MetaApp)
class MetaAppAdmin(admin.ModelAdmin):
    form = MetaAppForm
    list_display = ("name", "app_id", "token_type", "graph_version")
    readonly_fields = ("callback_url", "verify_token")
    fieldsets = (
        (None, {"fields": ("name", "app_id", "app_secret", "graph_version")}),
        ("Access token", {"fields": ("access_token", "token_type", "token_expires_at")}),
        (
            "Webhook setup",
            {
                "fields": ("callback_url", "verify_token"),
                "description": (
                    "Paste these values in your Meta app: Webhooks → Page → Callback URL."
                ),
            },
        ),
    )

    @admin.display(description="Callback URL")
    def callback_url(self, obj: MetaApp) -> str:
        return obj.webhook_url() if obj.pk else "Available after saving."

    def save_model(self, request, obj, form, change):
        if not change:
            obj.workspace = get_default_workspace()
        super().save_model(request, obj, form, change)


class PageForm(SecretAwareModelForm):
    secret_fields = ("page_token",)

    class Meta:
        model = Page
        fields = "__all__"


@admin.register(Page)
class PageAdmin(admin.ModelAdmin):
    form = PageForm
    list_display = ("name", "page_id", "subscribed", "last_lead_at", "error")
    list_filter = ("subscribed", "meta_app")
    search_fields = ("name", "page_id")
    filter_horizontal = ("default_destinations",)
    readonly_fields = ("last_checked_at", "last_lead_at", "error")


@admin.register(Form)
class FormAdmin(admin.ModelAdmin):
    list_display = ("name", "form_id", "page", "status")
    list_filter = ("page", "status")
    search_fields = ("name", "form_id")
    filter_horizontal = ("destinations",)
    readonly_fields = ("last_synced_at",)
