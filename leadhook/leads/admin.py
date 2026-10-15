from django.contrib import admin

from leadhook.destinations.admin import DeliveryInline
from leadhook.destinations.tasks import route_lead

from .models import Lead, WebhookEvent
from .tasks import fetch_lead


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
            if lead.status == Lead.Status.RECEIVED:
                fetch_lead.delay(lead.pk)
            elif lead.status in (Lead.Status.FETCHED, Lead.Status.UNROUTED):
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

    def has_add_permission(self, request):
        return False
