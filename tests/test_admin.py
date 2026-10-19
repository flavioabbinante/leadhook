from datetime import timedelta
from unittest import mock

from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from leadhook.destinations.models import Delivery, Destination
from leadhook.leads.models import Lead, WebhookEvent
from leadhook.meta.models import MetaApp
from tests.factories import make_destination, make_lead, make_meta_app


def test_admin_pages_render(admin_client):
    lead = make_lead()
    destination = make_destination()
    Delivery.objects.create(lead=lead, destination=destination)
    for name in [
        "meta_metaapp",
        "meta_page",
        "meta_form",
        "leads_lead",
        "leads_webhookevent",
        "destinations_destination",
        "destinations_delivery",
    ]:
        assert admin_client.get(reverse(f"admin:{name}_changelist")).status_code == 200
        assert admin_client.get(reverse(f"admin:{name}_add")).status_code in (200, 403)


def test_meta_app_change_page_never_shows_secrets(admin_client):
    app = make_meta_app()
    content = admin_client.get(reverse("admin:meta_metaapp_change", args=[app.pk])).content.decode()
    assert "app-secret" not in content
    assert "system-user-token" not in content
    assert "••••cret" in content
    assert app.webhook_url() in content
    assert app.verify_token in content


def test_saving_meta_app_without_secrets_keeps_them(admin_client):
    app = make_meta_app()
    response = admin_client.post(
        reverse("admin:meta_metaapp_change", args=[app.pk]),
        {
            "name": "Renamed",
            "app_id": "1000",
            "app_secret": "",
            "access_token": "",
            "token_type": MetaApp.TokenType.SYSTEM_USER,
            "token_expires_at_0": "",
            "token_expires_at_1": "",
            "graph_version": "v21.0",
        },
    )
    assert response.status_code == 302
    app.refresh_from_db()
    assert app.name == "Renamed"
    assert app.app_secret == "app-secret"
    assert app.access_token == "system-user-token"


def test_new_meta_app_gets_default_workspace(admin_client):
    response = admin_client.post(
        reverse("admin:meta_metaapp_add"),
        {
            "name": "Mine",
            "app_id": "42",
            "app_secret": "s",
            "access_token": "t",
            "token_type": MetaApp.TokenType.SYSTEM_USER,
            "token_expires_at_0": "",
            "token_expires_at_1": "",
            "graph_version": "v21.0",
        },
    )
    assert response.status_code == 302
    assert MetaApp.objects.get(app_id="42").workspace.name == "Default"


def test_destination_payload_preview(admin_client):
    make_lead()
    destination = make_destination(payload_template='{"who": {{ data.full_name | tojson }}}')
    content = admin_client.get(
        reverse("admin:destinations_destination_change", args=[destination.pk])
    ).content.decode()
    assert "Jane Doe" in content


def test_destination_payload_preview_shows_template_errors(admin_client):
    make_lead()
    destination = make_destination(payload_template='{"who": {{ data.full_name }')
    content = admin_client.get(
        reverse("admin:destinations_destination_change", args=[destination.pk])
    ).content.decode()
    assert "Template error" in content


def test_resend_action(admin_client):
    delivery = Delivery.objects.create(
        lead=make_lead(status=Lead.Status.FAILED),
        destination=make_destination(),
        status=Delivery.Status.FAILED,
    )
    with mock.patch("leadhook.destinations.admin.deliver.delay") as delay:
        response = admin_client.post(
            reverse("admin:destinations_delivery_changelist"),
            {"action": "resend", "_selected_action": [str(delivery.pk)]},
        )
    assert response.status_code == 302
    delay.assert_called_once_with(delivery.pk)
    delivery.refresh_from_db()
    assert delivery.status == Delivery.Status.PENDING


def test_reprocess_action_dispatches_by_status(admin_client):
    received = Lead.objects.create(leadgen_id="1")
    unrouted = make_lead(leadgen_id="2", status=Lead.Status.UNROUTED)
    delivered = make_lead(leadgen_id="3", form=unrouted.form, status=Lead.Status.DELIVERED)
    with (
        mock.patch("leadhook.leads.admin.fetch_lead.delay") as fetch,
        mock.patch("leadhook.leads.admin.route_lead.delay") as route,
    ):
        admin_client.post(
            reverse("admin:leads_lead_changelist"),
            {
                "action": "reprocess",
                "_selected_action": [str(received.pk), str(unrouted.pk), str(delivered.pk)],
            },
        )
    fetch.assert_called_once_with(received.pk)
    route.assert_called_once_with(unrouted.pk)


def test_reprocess_action_redispatches_routed_leads(admin_client):
    routed = make_lead(status=Lead.Status.ROUTED)
    with mock.patch("leadhook.leads.admin.route_lead.delay") as route:
        admin_client.post(
            reverse("admin:leads_lead_changelist"),
            {"action": "reprocess", "_selected_action": [str(routed.pk)]},
        )
    route.assert_called_once_with(routed.pk)


def test_destination_form_validates_auth(admin_client):
    response = admin_client.post(
        reverse("admin:destinations_destination_add"),
        {
            "name": "x",
            "kind": "webhook",
            "url": "https://a.example.com",
            "method": "POST",
            "auth_type": Destination.AuthType.BEARER,
            "auth_header_name": "",
            "secret": "",
            "extra_headers": "{}",
            "payload_template": "",
            "success_condition": "",
            "timeout_s": "15",
            "active": "on",
        },
    )
    assert response.status_code == 200
    assert "Required for the selected authentication." in response.content.decode()


def test_resend_action_skips_deliveries_in_progress_or_scheduled(admin_client):
    lead = make_lead(status=Lead.Status.PARTIALLY_FAILED)
    failed = Delivery.objects.create(
        lead=lead, destination=make_destination(name="a"), status=Delivery.Status.FAILED
    )
    succeeded = Delivery.objects.create(
        lead=lead, destination=make_destination(name="b"), status=Delivery.Status.SUCCESS
    )
    lease = timezone.now() + timedelta(seconds=60)
    in_flight = Delivery.objects.create(
        lead=lead, destination=make_destination(name="c"), attempts=1, locked_until=lease
    )
    with mock.patch("leadhook.destinations.admin.deliver.delay") as delay:
        response = admin_client.post(
            reverse("admin:destinations_delivery_changelist"),
            {
                "action": "resend",
                "_selected_action": [str(failed.pk), str(succeeded.pk), str(in_flight.pk)],
            },
        )
    assert sorted(c.args[0] for c in delay.call_args_list) == sorted([failed.pk, succeeded.pk])
    in_flight.refresh_from_db()
    assert in_flight.locked_until == lease
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any(
        "2 deliveries queued" in m
        and "1 skipped" in m
        and "in progress, scheduled, or lead data purged" in m
        for m in messages
    )


def test_process_events_action(admin_client):
    pending = WebhookEvent.objects.create(payload={}, signature_ok=True)
    done = WebhookEvent.objects.create(payload={}, signature_ok=True, processed_at=timezone.now())
    unsigned = WebhookEvent.objects.create(payload={}, signature_ok=False)
    with mock.patch("leadhook.leads.admin.process_event.delay") as delay:
        response = admin_client.post(
            reverse("admin:leads_webhookevent_changelist"),
            {
                "action": "process_events",
                "_selected_action": [str(pending.pk), str(done.pk), str(unsigned.pk)],
            },
        )
    delay.assert_called_once_with(pending.pk)
    messages = [str(m) for m in get_messages(response.wsgi_request)]
    assert any("1 event(s) queued" in m and "2 skipped" in m for m in messages)


def test_destination_extra_headers_help_warns_about_clear_text(admin_client):
    content = admin_client.get(reverse("admin:destinations_destination_add")).content.decode()
    assert "Stored in clear text" in content
    assert "put credentials in Secret, not here" in content
