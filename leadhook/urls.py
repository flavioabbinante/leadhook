from django.contrib import admin
from django.contrib.auth import views as auth_views
from django.urls import path
from django.views.generic import RedirectView

from leadhook.core.diagnostics import diagnostics_view
from leadhook.core.invitations import accept_invitation
from leadhook.core.twofactor import two_factor
from leadhook.core.views import ThrottledPasswordResetView, health
from leadhook.leads.stats import stats_view
from leadhook.meta.facebook_login import facebook_callback, facebook_login
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
    path("admin/setup/facebook/", admin.site.admin_view(facebook_login), name="facebook-login"),
    path(
        "admin/setup/facebook/callback/",
        admin.site.admin_view(facebook_callback),
        name="facebook-callback",
    ),
    path("admin/routing/", admin.site.admin_view(routing_view), name="routing"),
    path("admin/stats/", admin.site.admin_view(stats_view), name="stats"),
    path("admin/diagnostics/", admin.site.admin_view(diagnostics_view), name="diagnostics"),
    path("admin/2fa/", admin.site.admin_view(two_factor), name="two-factor"),
    # The admin login page links to "admin_password_reset" once that URL exists.
    path(
        "admin/password_reset/", ThrottledPasswordResetView.as_view(), name="admin_password_reset"
    ),
    path(
        "admin/password_reset/done/",
        auth_views.PasswordResetDoneView.as_view(),
        name="password_reset_done",
    ),
    path(
        "admin/reset/<uidb64>/<token>/",
        auth_views.PasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "admin/reset/done/",
        auth_views.PasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("admin/", admin.site.urls),
    path("invite/<str:token>/", accept_invitation, name="accept-invitation"),
    path("health/", health, name="health"),
    path("webhooks/meta/<uuid:webhook_uuid>/", meta_webhook, name="meta-webhook"),
]
