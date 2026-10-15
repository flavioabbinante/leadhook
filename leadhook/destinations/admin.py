import json

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html

from leadhook.core.admin_forms import SecretAwareModelForm
from leadhook.core.models import get_default_workspace
from leadhook.leads.models import Lead

from .models import Delivery, Destination
from .payload import build_context, render_body
from .services import reset_for_resend
from .tasks import deliver
from .templating import TemplateError


class DestinationForm(SecretAwareModelForm):
    secret_fields = ("secret",)

    class Meta:
        model = Destination
        exclude = ("workspace",)


@admin.register(Destination)
class DestinationAdmin(admin.ModelAdmin):
    form = DestinationForm
    list_display = ("name", "url", "auth_type", "active")
    list_filter = ("active", "auth_type")
    search_fields = ("name", "url")
    readonly_fields = ("payload_preview",)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.workspace = get_default_workspace()
        super().save_model(request, obj, form, change)

    def get_form(self, request, obj=None, **kwargs):
        self._preview_lead_id = request.GET.get("preview_lead")
        return super().get_form(request, obj, **kwargs)

    @admin.display(description="Payload preview")
    def payload_preview(self, obj: Destination):
        if not obj.pk:
            return "Save the destination to see a preview."
        leads = Lead.objects.exclude(status=Lead.Status.RECEIVED)
        lead_id = getattr(self, "_preview_lead_id", None)
        lead = leads.filter(leadgen_id=lead_id).first() if lead_id else leads.first()
        if lead is None:
            return "No fetched leads yet."
        context = build_context(
            lead, Delivery(lead=lead, destination=obj), attempt=1, sent_at=timezone.now()
        )
        try:
            body = json.dumps(json.loads(render_body(obj, context)), indent=2, ensure_ascii=False)
        except TemplateError as exc:
            return str(exc)
        return format_html(
            "<p>Using lead {} (add <code>?preview_lead=&lt;lead id&gt;</code> "
            "to the URL to pick another).</p><pre>{}</pre>",
            lead.leadgen_id,
            body,
        )


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "lead",
        "destination",
        "status",
        "attempts",
        "last_status_code",
        "next_retry_at",
        "updated_at",
    )
    list_filter = ("status", "destination")
    search_fields = ("lead__leadgen_id",)
    actions = ["resend"]
    readonly_fields = [f.name for f in Delivery._meta.fields]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Resend selected deliveries")
    def resend(self, request, queryset):
        count = 0
        for delivery in queryset.select_related("lead"):
            reset_for_resend(delivery)
            deliver.delay(delivery.pk)
            count += 1
        self.message_user(request, f"{count} deliveries queued for resending.")


class DeliveryInline(admin.TabularInline):
    model = Delivery
    extra = 0
    can_delete = False
    fields = ("destination", "status", "attempts", "last_status_code", "last_error", "delivered_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False
