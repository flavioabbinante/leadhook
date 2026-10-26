"""Routing screen: connect page defaults and forms to destinations in one place."""

from django.contrib import messages
from django.contrib.admin import site
from django.core.exceptions import PermissionDenied
from django.db.models import Count, Prefetch
from django.shortcuts import redirect
from django.template.response import TemplateResponse

from leadhook.destinations.models import Destination
from leadhook.leads.models import Lead
from leadhook.meta.models import Form, Page

TEMPLATE = "admin/leadhook_routing.html"
REQUIRED_PERMISSIONS = ("meta.change_page", "meta.change_form")


def _source(value: str) -> Page | Form | None:
    """'page:<pk>' is the page default (all forms without their own), 'form:<pk>' one form."""
    kind, _, pk = value.partition(":")
    if not pk.isdigit():
        return None
    model = {"page": Page, "form": Form}.get(kind)
    return model.objects.filter(pk=pk).first() if model else None


def _relation(source: Page | Form):
    return source.default_destinations if isinstance(source, Page) else source.destinations


def _describe(source: Page | Form) -> str:
    return f"di tutti i form di {source}" if isinstance(source, Page) else f"del form {source}"


def _unrouted(source: Page | Form):
    page = source if isinstance(source, Page) else source.page
    return Lead.objects.filter(page=page, status=Lead.Status.UNROUTED, purged_at__isnull=True)


def _has_route(form: Form | None, page_destinations: list) -> bool:
    """Same rule as resolve_destinations: own form destinations win, even paused ones."""
    own = list(form.destinations.all()) if form is not None else []
    return any(destination.active for destination in own or page_destinations)


def routing_view(request):
    if request.method == "POST":
        if not request.user.has_perms(REQUIRED_PERMISSIONS):
            raise PermissionDenied
        _handle(request)
        return redirect("routing")

    destinations = Destination.objects.order_by("name")
    ordered = destinations.only("pk", "name", "active")
    pages = Page.objects.order_by("name", "page_id").prefetch_related(
        Prefetch("default_destinations", queryset=ordered),
        Prefetch(
            "forms",
            queryset=Form.objects.order_by("name", "form_id").prefetch_related(
                Prefetch("destinations", queryset=ordered)
            ),
        ),
    )
    waiting = dict(
        Lead.objects.filter(status=Lead.Status.UNROUTED, purged_at__isnull=True)
        .values_list("page")
        .annotate(n=Count("pk"))
    )
    # (page, form) of the waiting leads, to tell whether "Deliver now" would send any of them.
    waiting_sources = set(
        Lead.objects.filter(status=Lead.Status.UNROUTED, purged_at__isnull=True)
        .values_list("page", "form")
        .distinct()
    )
    rows = []
    for page in pages:
        page_destinations = list(page.default_destinations.all())
        forms = {form.pk: form for form in page.forms.all()}
        rows.append(
            {
                "kind": "page",
                "source": page,
                "key": f"page:{page.pk}",
                "destinations": page_destinations,
                "waiting": waiting.get(page.pk, 0),
                "routable": any(
                    _has_route(forms.get(form_pk), page_destinations)
                    for page_pk, form_pk in waiting_sources
                    if page_pk == page.pk
                ),
            }
        )
        for form in page.forms.all():
            own = list(form.destinations.all())
            rows.append(
                {
                    "kind": "form",
                    "source": form,
                    "key": f"form:{form.pk}",
                    "destinations": own,
                    "inherited": [] if own else page_destinations,
                }
            )
    context = {
        **site.each_context(request),
        "title": "Routing",
        "rows": rows,
        "pages": pages,
        # Active first, so the menu does not preselect a paused destination.
        "destinations": destinations.order_by("-active", "name"),
        "can_change": request.user.has_perms(REQUIRED_PERMISSIONS),
    }
    return TemplateResponse(request, TEMPLATE, context)


def _handle(request) -> None:
    from leadhook.destinations.tasks import route_lead

    action = request.POST.get("action")
    source = _source(request.POST.get("source", ""))
    if source is None:
        messages.error(request, "Scegli da dove arrivano i lead.")
        return
    if action == "deliver_waiting":
        leads = list(_unrouted(source).values_list("pk", flat=True))
        for pk in leads:
            route_lead.delay(pk)
        page = source.page if isinstance(source, Form) else source
        messages.success(request, f"{len(leads)} lead in attesa di {page} inviati al routing.")
        return
    destination = Destination.objects.filter(pk=request.POST.get("destination") or 0).first()
    if destination is None:
        messages.error(request, "Scegli una destinazione.")
        return
    if action == "connect":
        _relation(source).add(destination)
        messages.success(request, f"I lead {_describe(source)} ora vanno a {destination}.")
        waiting = _unrouted(source).count()
        if waiting:
            messages.warning(
                request,
                f"{waiting} lead di questa pagina erano in attesa di una destinazione: "
                "premi “Consegna ora” sulla riga della pagina per inviarli.",
            )
    elif action == "disconnect":
        _relation(source).remove(destination)
        messages.success(request, f"{destination} non riceve più i lead {_describe(source)}.")
    else:
        messages.error(request, "Azione sconosciuta.")
