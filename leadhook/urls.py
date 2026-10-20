from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

from leadhook.core.invitations import accept_invitation
from leadhook.core.views import health
from leadhook.meta.setup import setup_view
from leadhook.meta.views import meta_webhook
from leadhook.routing.views import routing_view

admin.site.site_header = "LeadHook"
admin.site.site_title = "LeadHook"
admin.site.index_title = "Dashboard"
# The LeadHook shell (templates/admin/base.html) has its own sidebar.
admin.site.enable_nav_sidebar = False
admin.site.index_template = "admin/leadhook_index.html"

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("admin/setup/", admin.site.admin_view(setup_view), name="setup"),
    path("admin/routing/", admin.site.admin_view(routing_view), name="routing"),
    path("admin/", admin.site.urls),
    path("invite/<str:token>/", accept_invitation, name="accept-invitation"),
    path("health/", health, name="health"),
    path("webhooks/meta/<uuid:webhook_uuid>/", meta_webhook, name="meta-webhook"),
]
