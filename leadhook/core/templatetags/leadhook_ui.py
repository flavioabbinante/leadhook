from django import template
from django.urls import reverse

register = template.Library()

# Sidebar sections: (heading, [(url name, label, icon, permission)]).
NAVIGATION = [
    (
        "",
        [
            ("admin:index", "Dashboard", "home", ""),
            ("setup", "Setup", "wand", ""),
        ],
    ),
    (
        "Pipeline",
        [
            ("admin:leads_lead_changelist", "Leads", "inbox", "leads.view_lead"),
            (
                "admin:destinations_delivery_changelist",
                "Deliveries",
                "send",
                "destinations.view_delivery",
            ),
            (
                "admin:leads_webhookevent_changelist",
                "Webhook events",
                "zap",
                "leads.view_webhookevent",
            ),
        ],
    ),
    (
        "Configuration",
        [
            ("routing", "Routing", "shuffle", "meta.view_form"),
            (
                "admin:destinations_destination_changelist",
                "Destinations",
                "target",
                "destinations.view_destination",
            ),
            ("admin:meta_page_changelist", "Pages", "flag", "meta.view_page"),
            ("admin:meta_form_changelist", "Forms", "list", "meta.view_form"),
            ("admin:meta_metaapp_changelist", "Meta apps", "box", "meta.view_metaapp"),
        ],
    ),
    (
        "Account",
        [
            ("admin:auth_user_changelist", "Users", "users", "auth.view_user"),
            ("admin:auth_group_changelist", "Groups", "shield", "auth.view_group"),
            ("admin:core_invitation_changelist", "Invitations", "mail", "core.view_invitation"),
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
