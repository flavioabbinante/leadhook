from django.contrib import admin, messages
from django.urls import reverse
from django.utils.html import format_html

from leadhook.core.admin_forms import SecretAwareModelForm
from leadhook.core.models import get_default_workspace
from leadhook.leads.models import Lead

from .models import Form, MetaApp, Page


def _offer_unrouted_leads(modeladmin, request, leads, lead_filter: str) -> None:
    """Spec §4: once destinations exist, point to the leads that waited for one."""
    count = leads.filter(status=Lead.Status.UNROUTED).count()
    if not count:
        return
    url = reverse("admin:leads_lead_changelist") + f"?status__exact=unrouted&{lead_filter}"
    modeladmin.message_user(
        request,
        format_html(
            '{} lead senza routing ora possono essere consegnati: <a href="{}">selezionali '
            "ed esegui "
            "“Rielabora i lead selezionati”</a>.",
            count,
            url,
        ),
        messages.WARNING,
    )


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
        (None, {"fields": ("name", "app_id", "app_secret", "graph_version", "login_config_id")}),
        (
            "Token di accesso",
            {
                "fields": ("access_token", "token_type", "token_expires_at"),
                "description": "Compilato da “Collega con Facebook” nella configurazione guidata.",
            },
        ),
        (
            "Configurazione webhook",
            {
                "fields": ("callback_url", "verify_token"),
                "description": (
                    "Incolla questi valori nella tua app Meta: Webhooks → Page → Callback URL."
                ),
            },
        ),
    )

    @admin.display(description="URL di callback")
    def callback_url(self, obj: MetaApp) -> str:
        return obj.webhook_url() if obj.pk else "Disponibile dopo il salvataggio."

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
    readonly_fields = ("last_checked_at", "last_lead_at", "error", "silence_alerted_at")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        page = form.instance
        if page.default_destinations.filter(active=True).exists():
            _offer_unrouted_leads(self, request, page.leads.all(), f"page__id__exact={page.pk}")


@admin.register(Form)
class FormAdmin(admin.ModelAdmin):
    list_display = ("name", "form_id", "page", "status")
    list_filter = ("page", "status")
    search_fields = ("name", "form_id")
    filter_horizontal = ("destinations",)
    readonly_fields = ("last_synced_at", "last_reconciled_at")

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        lead_form = form.instance
        if lead_form.destinations.filter(active=True).exists():
            _offer_unrouted_leads(
                self, request, lead_form.leads.all(), f"form__id__exact={lead_form.pk}"
            )
