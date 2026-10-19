import json

from django.contrib import admin
from django.utils import timezone
from django.utils.html import format_html, format_html_join

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
        help_texts = {
            "extra_headers": (
                'Additional headers as a JSON object, e.g. {"X-Source": "leadhook"}. '
                "Stored in clear text — put credentials in Secret, not here."
            ),
        }


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

    def get_object(self, request, object_id, from_field=None):
        obj = super().get_object(request, object_id, from_field)
        if obj is not None:
            # Per request: the admin instance is shared between threads.
            obj._preview_lead_id = request.GET.get("preview_lead")
        return obj

    @admin.display(description="Payload preview")
    def payload_preview(self, obj: Destination):
        if not obj.pk:
            return "Save the destination to see a preview."
        leads = Lead.objects.exclude(status=Lead.Status.RECEIVED).filter(purged_at__isnull=True)
        lead_id = getattr(obj, "_preview_lead_id", None)
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
        others = format_html_join(
            " · ",
            '<a href="?preview_lead={}">{}</a>',
            ((other.leadgen_id, other.leadgen_id) for other in leads[:10]),
        )
        return format_html(
            "<p>Using lead {}. Preview another: {}</p><pre>{}</pre>", lead.leadgen_id, others, body
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
        count = skipped = 0
        for delivery in queryset.select_related("lead"):
            # Pending deliveries are being sent or wait for a retry; resetting them would
            # break their lease, so only finished (success/failed) ones are resent.
            if reset_for_resend(delivery):
                deliver.delay(delivery.pk)
                count += 1
            else:
                skipped += 1
        message = f"{count} deliveries queued for resending."
        if skipped:
            message += f" {skipped} skipped (in progress, scheduled, or lead data purged)."
        self.message_user(request, message)


class DeliveryInline(admin.TabularInline):
    model = Delivery
    extra = 0
    can_delete = False
    fields = ("destination", "status", "attempts", "last_status_code", "last_error", "delivered_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False
