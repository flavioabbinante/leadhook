from django.contrib import admin

from leadhook.destinations.admin import DeliveryInline
from leadhook.destinations.tasks import route_lead

from .models import Lead, WebhookEvent
from .tasks import fetch_lead, process_event


@admin.register(Lead)
class LeadAdmin(admin.ModelAdmin):
    list_display = ("leadgen_id", "page", "form", "status", "source", "received_at")
    list_filter = ("status", "source", "page", "form")
    search_fields = ("leadgen_id",)
    inlines = [DeliveryInline]
    actions = ["reprocess"]
    readonly_fields = [f.name for f in Lead._meta.fields]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Reprocess selected leads")
    def reprocess(self, request, queryset):
        count = 0
        for lead in queryset:
            if lead.purged_at is not None:
                continue
            if lead.status == Lead.Status.RECEIVED:
                fetch_lead.delay(lead.pk)
            elif lead.status in (Lead.Status.FETCHED, Lead.Status.UNROUTED, Lead.Status.ROUTED):
                # For ROUTED leads route_lead re-dispatches pending deliveries that are due.
                route_lead.delay(lead.pk)
            else:
                continue
            count += 1
        self.message_user(
            request,
            f"{count} lead(s) queued. Delivered or failed leads are resent from Deliveries.",
        )


@admin.register(WebhookEvent)
class WebhookEventAdmin(admin.ModelAdmin):
    list_display = ("id", "meta_app", "received_at", "signature_ok", "processed_at")
    list_filter = ("signature_ok",)
    readonly_fields = [f.name for f in WebhookEvent._meta.fields]
    actions = ["process_events"]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Process selected events")
    def process_events(self, request, queryset):
        count = skipped = 0
        for event in queryset:
            if event.signature_ok and event.processed_at is None:
                process_event.delay(event.pk)
                count += 1
            else:
                skipped += 1
        message = f"{count} event(s) queued for processing."
        if skipped:
            message += f" {skipped} skipped (already processed or invalid signature)."
        self.message_user(request, message)
