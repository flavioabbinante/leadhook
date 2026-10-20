"""Guided first-run setup in the admin (spec §10)."""

from django.contrib import messages
from django.contrib.admin import site
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.shortcuts import redirect
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.html import format_html

from leadhook.core.admin_forms import SecretAwareModelForm
from leadhook.core.models import get_default_workspace
from leadhook.destinations.models import Destination
from leadhook.leads.models import Lead

from .graph import GraphClient, GraphError, app_subscriptions, client_for_page
from .models import Form, MetaApp, Page
from .services import connect_page

TEMPLATE = "admin/leadhook_setup.html"


class AppCredentialsForm(SecretAwareModelForm):
    secret_fields = ("app_secret",)

    class Meta:
        model = MetaApp
        fields = ("app_id", "app_secret")


def setup_status() -> dict:
    meta_app = MetaApp.objects.order_by("pk").first()
    return {
        "meta_app": meta_app,
        "has_credentials": bool(meta_app and meta_app.app_id and meta_app.app_secret),
        "has_app": bool(meta_app and meta_app.access_token),
        "has_pages": Page.objects.filter(subscribed=True).exists(),
        "has_destination": Destination.objects.filter(active=True)
        .filter(Q(default_for_pages__isnull=False) | Q(forms__isnull=False))
        .exists(),
        "has_lead": Lead.objects.exists(),
    }


def setup_complete() -> bool:
    status = setup_status()
    return status["has_app"] and status["has_pages"] and status["has_destination"]


def setup_view(request):
    status = setup_status()
    meta_app = status["meta_app"]
    context = {
        **site.each_context(request),
        **status,
        "title": "Set up LeadHook",
        "login_redirect_url": MetaApp.login_redirect_url(),
        "available_pages": None,
        "forms": Form.objects.filter(page__subscribed=True)
        .select_related("page")
        .order_by("page__name", "name"),
    }
    app_form = AppCredentialsForm(instance=meta_app)
    if request.method == "POST" and request.POST.get("action") == "save_app":
        if not request.user.has_perm(
            "meta.add_metaapp" if meta_app is None else "meta.change_metaapp"
        ):
            raise PermissionDenied
        instance = meta_app or MetaApp(workspace=get_default_workspace(), name="Meta app")
        app_form = AppCredentialsForm(request.POST, instance=instance)
        if app_form.is_valid():
            app_form.save()
            return redirect("facebook-login")
    elif request.method == "POST" and meta_app is not None:
        action = request.POST.get("action")
        if action == "verify":
            _verify_webhook(request, meta_app)
        elif action == "load_pages":
            pages = _load_pages(request, meta_app)
            if pages is not None:
                return TemplateResponse(request, TEMPLATE, {**context, "available_pages": pages})
        elif action == "connect_pages":
            _connect_pages(request, meta_app, request.POST.getlist("page_ids"))
        elif action == "test_lead":
            _send_test_lead(request, request.POST.get("form_id", ""))
        return redirect("setup")
    context["app_form"] = app_form
    if request.GET.get("load") == "pages" and status["has_app"]:
        # Straight after “Connect with Facebook”: show the pages without another click.
        context["available_pages"] = _load_pages(request, meta_app)
    return TemplateResponse(request, TEMPLATE, context)


def _verify_webhook(request, meta_app: MetaApp) -> None:
    try:
        subscriptions = app_subscriptions(meta_app)
    except GraphError as exc:
        messages.error(request, f"Could not read the app's webhook subscriptions: {exc}")
        return
    for subscription in subscriptions:
        fields = {f.get("name") for f in subscription.get("fields") or [] if isinstance(f, dict)}
        if (
            subscription.get("object") == "page"
            and subscription.get("callback_url") == meta_app.webhook_url()
            and "leadgen" in fields
        ):
            messages.success(request, "Webhook verified: Meta sends page leadgen events here.")
            return
    messages.error(
        request,
        "Meta has no Page subscription with the leadgen field for this callback URL yet. "
        "Complete the webhook step, then verify again.",
    )


def _user_pages(meta_app: MetaApp) -> list[dict]:
    return GraphClient(meta_app.graph_version, meta_app.access_token).list_pages()


def _load_pages(request, meta_app: MetaApp) -> list[dict] | None:
    try:
        pages = _user_pages(meta_app)
    except GraphError as exc:
        messages.error(request, f"Could not list pages: {exc}")
        return None
    connected = set(Page.objects.values_list("page_id", flat=True))
    # Only id and name: page tokens stay server side and are fetched again on connect.
    return [
        {"id": str(p["id"]), "name": p.get("name", ""), "connected": str(p["id"]) in connected}
        for p in pages
        if p.get("id")
    ]


def _connect_pages(request, meta_app: MetaApp, page_ids: list[str]) -> None:
    if not page_ids:
        messages.warning(request, "Select at least one page.")
        return
    try:
        available = {str(p.get("id")): p for p in _user_pages(meta_app)}
    except GraphError as exc:
        messages.error(request, f"Could not list pages: {exc}")
        return
    for page_id in page_ids:
        raw = available.get(page_id)
        if raw is None:
            messages.error(request, f"Page {page_id} is not reachable with the app's token.")
            continue
        try:
            page, form_count = connect_page(meta_app, raw)
        except GraphError as exc:
            messages.error(request, f"Page {raw.get('name') or page_id}: {exc}")
            continue
        messages.success(request, f"Connected {page} with {form_count} form(s).")


def _send_test_lead(request, form_id: str) -> None:
    form = Form.objects.select_related("page__meta_app").filter(form_id=form_id).first()
    if form is None:
        messages.error(request, "Choose a form.")
        return
    try:
        client_for_page(form.page).create_test_lead(form.form_id)
    except GraphError as exc:
        messages.error(
            request,
            f"Meta refused the test lead: {exc}. Meta keeps one test lead per form: delete "
            "the previous one in the Lead Ads Testing Tool and try again.",
        )
        return
    messages.success(
        request,
        format_html(
            'Test lead requested. It appears in <a href="{}">Leads</a> as soon as Meta sends '
            "the webhook, usually within a minute.",
            reverse("admin:leads_lead_changelist"),
        ),
    )
