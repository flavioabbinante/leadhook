from django import template
from django.urls import reverse

register = template.Library()

# Sidebar sections: (heading, [(url name, label, icon, permission)]).
NAVIGATION = [
    (
        "",
        [
            ("admin:index", "Dashboard", "home", ""),
            ("setup", "Configurazione guidata", "wand", ""),
        ],
    ),
    (
        "Flusso",
        [
            ("admin:leads_lead_changelist", "Lead", "inbox", "leads.view_lead"),
            (
                "admin:destinations_delivery_changelist",
                "Consegne",
                "send",
                "destinations.view_delivery",
            ),
            (
                "admin:leads_webhookevent_changelist",
                "Eventi webhook",
                "zap",
                "leads.view_webhookevent",
            ),
        ],
    ),
    (
        "Configurazione",
        # In setup order: connect Meta, pick pages and forms, add a CRM, then route.
        [
            ("admin:meta_metaapp_changelist", "App Meta", "box", "meta.view_metaapp"),
            ("admin:meta_page_changelist", "Pagine", "flag", "meta.view_page"),
            ("admin:meta_form_changelist", "Form", "list", "meta.view_form"),
            (
                "admin:destinations_destination_changelist",
                "Destinazioni",
                "target",
                "destinations.view_destination",
            ),
            ("routing", "Routing", "shuffle", "meta.view_form"),
        ],
    ),
    (
        "Account",
        [
            ("admin:auth_user_changelist", "Utenti", "users", "auth.view_user"),
            ("admin:core_invitation_changelist", "Inviti", "mail", "core.view_invitation"),
            ("admin:auth_group_changelist", "Gruppi", "shield", "auth.view_group"),
        ],
    ),
]


@register.inclusion_tag("leadhook/sidebar_nav.html", takes_context=True)
def leadhook_sidebar(context) -> dict:
    request = context["request"]
    home = reverse("admin:index")
    sections = []
    for heading, entries in NAVIGATION:
        items = []
        for name, label, icon, permission in entries:
            if permission and not request.user.has_perm(permission):
                continue
            url = reverse(name)
            # The dashboard URL prefixes every admin URL, so it is active only on an exact match.
            active = request.path == url if url == home else request.path.startswith(url)
            items.append({"url": url, "label": label, "icon": icon, "active": active})
        if items:
            sections.append({"heading": heading, "items": items})
    return {"sections": sections}


@register.simple_tag
def trend(current: int, previous: int) -> str:
    """'+55%' style change against the previous period, or '' when there is nothing to compare."""
    if not previous:
        return ""
    change = round((current - previous) * 100 / previous)
    return f"{change:+d}%"
