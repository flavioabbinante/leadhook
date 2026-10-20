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
        "title": "Configurazione guidata",
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
        instance = meta_app or MetaApp(workspace=get_default_workspace(), name="App Meta")
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
        messages.error(request, f"Impossibile leggere le iscrizioni webhook dell'app: {exc}")
        return
    for subscription in subscriptions:
        fields = {f.get("name") for f in subscription.get("fields") or [] if isinstance(f, dict)}
        if (
            subscription.get("object") == "page"
            and subscription.get("callback_url") == meta_app.webhook_url()
            and "leadgen" in fields
        ):
            messages.success(
                request, "Webhook verificato: Meta invia qui gli eventi leadgen della pagina."
            )
            return
    messages.error(
        request,
        "Meta non ha ancora un'iscrizione Page con il campo leadgen per questo URL di callback. "
        "Completa il passaggio del webhook, poi verifica di nuovo.",
    )


def _user_pages(meta_app: MetaApp) -> list[dict]:
    return GraphClient(meta_app.graph_version, meta_app.access_token).list_pages()


def _load_pages(request, meta_app: MetaApp) -> list[dict] | None:
    try:
        pages = _user_pages(meta_app)
    except GraphError as exc:
        messages.error(request, f"Impossibile elencare le pagine: {exc}")
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
        messages.warning(request, "Seleziona almeno una pagina.")
        return
    try:
        available = {str(p.get("id")): p for p in _user_pages(meta_app)}
    except GraphError as exc:
        messages.error(request, f"Impossibile elencare le pagine: {exc}")
        return
    for page_id in page_ids:
        raw = available.get(page_id)
        if raw is None:
            messages.error(
                request, f"La pagina {page_id} non è raggiungibile con il token dell'app."
            )
            continue
        try:
            page, form_count = connect_page(meta_app, raw)
        except GraphError as exc:
            messages.error(request, f"Pagina {raw.get('name') or page_id}: {exc}")
            continue
        messages.success(request, f"Pagina {page} collegata, form trovati: {form_count}.")


def _send_test_lead(request, form_id: str) -> None:
    form = Form.objects.select_related("page__meta_app").filter(form_id=form_id).first()
    if form is None:
        messages.error(request, "Scegli un form.")
        return
    try:
        client_for_page(form.page).create_test_lead(form.form_id)
    except GraphError as exc:
        messages.error(
            request,
            f"Meta ha rifiutato il lead di prova: {exc}. Meta tiene un solo lead di prova "
            "per form: elimina quello precedente nel Lead Ads Testing Tool e riprova.",
        )
        return
    messages.success(
        request,
        format_html(
            'Lead di prova richiesto. Compare in <a href="{}">Lead</a> appena Meta invia il '
            "webhook, di solito entro un minuto.",
            reverse("admin:leads_lead_changelist"),
        ),
    )
