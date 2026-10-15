from django.contrib import admin
from django.urls import path
from django.views.generic import RedirectView

admin.site.site_header = "LeadHook"
admin.site.site_title = "LeadHook"
admin.site.index_title = "Administration"

urlpatterns = [
    path("", RedirectView.as_view(url="/admin/", permanent=False)),
    path("admin/", admin.site.urls),
]
