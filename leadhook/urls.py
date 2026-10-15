from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from leadhook.meta.views import meta_webhook

admin.site.site_header = "LeadHook"
admin.site.site_title = "LeadHook"
admin.site.index_title = "Administration"

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("admin/", admin.site.urls),
    path("webhooks/meta/<uuid:webhook_uuid>/", meta_webhook, name="meta-webhook"),
]
