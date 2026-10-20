from datetime import timedelta
from unittest import mock

from django.contrib import admin as django_admin
from django.contrib.messages import get_messages
from django.urls import reverse
from django.utils import timezone

from leadhook.destinations.models import Delivery, Destination
from leadhook.leads.models import Lead, WebhookEvent
from leadhook.meta.models import Form, MetaApp, Page
from tests.factories import make_destination, make_form, make_lead, make_meta_app, make_page


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


def test_destination_field_mappings_offer_form_questions(admin_client):
    make_lead()
    Form.objects.update(questions=[{"key": "e-mail", "label": "Email"}])
    destination = make_destination()
    content = admin_client.get(
        reverse("admin:destinations_destination_change", args=[destination.pk])
    ).content.decode()
    assert 'value="answer:e-mail"' in content
    assert "Email (e-mail)" in content


def test_destination_field_mappings_offer_only_routed_form_questions(admin_client):
    destination = make_destination()
    routed = make_lead().form
    routed.questions = [{"key": "e-mail", "label": "Email"}]
    routed.save()
    routed.destinations.add(destination)
    make_form(page=routed.page, form_id="999", questions=[{"key": "p_iva", "label": "P.Iva"}])
    content = admin_client.get(
        reverse("admin:destinations_destination_change", args=[destination.pk])
    ).content.decode()
    assert 'value="answer:e-mail"' in content
    assert "answer:p_iva" not in content


def test_destination_payload_preview_shows_template_errors(admin_client):
    make_lead()
    destination = make_destination(payload_template='{"who": {{ data.full_name }')
    content = admin_client.get(
        reverse("admin:destinations_destination_change", args=[destination.pk])
    ).content.decode()
    assert "Errore nel template" in content


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
    assert "autenticazione scelta." in response.content.decode()


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
        "2 consegne messe in coda" in m
        and "1 saltate" in m
        and "in corso, programmate o con i dati del lead eliminati" in m
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
    assert any("1 eventi messi in coda" in m and "2 saltati" in m for m in messages)


def test_destination_extra_headers_help_warns_about_clear_text(admin_client):
    content = admin_client.get(reverse("admin:destinations_destination_add")).content.decode()
    assert "Salvati in chiaro" in content
    assert "metti le credenziali in Chiave segreta, non qui" in content


def test_page_and_destination_change_pages_never_show_secrets(admin_client):
    page = make_page(page_token="page-token-secret-1234")
    destination = make_destination(
        auth_type=Destination.AuthType.BEARER, secret="destination-secret-5678"
    )
    for url, secret, tail in [
        (reverse("admin:meta_page_change", args=[page.pk]), "page-token-secret-1234", "1234"),
        (
            reverse("admin:destinations_destination_change", args=[destination.pk]),
            "destination-secret-5678",
            "5678",
        ),
    ]:
        html = admin_client.get(url).content.decode()
        assert secret not in html
        assert f"Salvato: ••••{tail}" in html


def test_saving_destination_without_secret_keeps_it(admin_client):
    destination = make_destination(auth_type=Destination.AuthType.BEARER, secret="keep-me")
    response = admin_client.post(
        reverse("admin:destinations_destination_change", args=[destination.pk]),
        {
            "name": "Renamed",
            "kind": "webhook",
            "url": destination.url,
            "method": "POST",
            "auth_type": "bearer",
            "auth_header_name": "",
            "secret": "",
            "extra_headers": "{}",
            "payload_template": "",
            "success_condition": "",
            "timeout_s": "15",
            "active": "on",
            "field_mappings-TOTAL_FORMS": "1",
            "field_mappings-INITIAL_FORMS": "0",
            "field_mappings-0-target": "fields.canale",
            "field_mappings-0-source": "fixed",
            "field_mappings-0-fixed_value": "9",
            "field_mappings-0-transform": "",
        },
    )
    assert response.status_code == 302
    assert destination.field_mappings.get().target == "fields.canale"
    destination.refresh_from_db()
    assert destination.name == "Renamed"
    assert destination.secret == "keep-me"


def test_dashboard_shows_counts_and_page_health(admin_client):
    lead = make_lead(status=Lead.Status.UNROUTED)
    Page.objects.filter(pk=lead.page.pk).update(error="Meta ha rifiutato il token della pagina: x")
    failed = make_lead(leadgen_id="2", form=lead.form, status=Lead.Status.FAILED)
    Delivery.objects.create(
        lead=failed, destination=make_destination(), status=Delivery.Status.FAILED
    )
    html = admin_client.get(reverse("admin:index")).content.decode()
    assert "Lead di oggi" in html
    assert "Meta ha rifiutato il token della pagina: x" in html
    assert 'href="/admin/routing/"' in html  # unrouted leads are fixed in Routing
    assert "?status__exact=failed" in html
    assert html.count('<span class="lh-kpi-value">1</span>') == 2  # failed deliveries, unrouted
    assert "Non attivi" in html  # no heartbeat recorded in tests


def test_payload_preview_lead_chooser_is_per_request(admin_client):
    destination = make_destination()
    first = make_lead(leadgen_id="1")
    make_lead(leadgen_id="2", form=first.form)
    url = reverse("admin:destinations_destination_change", args=[destination.pk])
    html = admin_client.get(url + "?preview_lead=1").content.decode()
    assert "Lead usato: 1" in html
    assert 'href="?preview_lead=2"' in html
    assert not hasattr(django_admin.site._registry[Destination], "_preview_lead_id")


def test_saving_form_with_destinations_offers_unrouted_leads(admin_client):
    lead = make_lead(status=Lead.Status.UNROUTED)
    form = lead.form
    destination = make_destination()
    response = admin_client.post(
        reverse("admin:meta_form_change", args=[form.pk]),
        {
            "page": form.page.pk,
            "form_id": form.form_id,
            "name": form.name,
            "locale": form.locale,
            "status": form.status,
            "destinations": [destination.pk],
        },
        follow=True,
    )
    text = " ".join(str(m) for m in get_messages(response.wsgi_request))
    assert "1 lead senza instradamento" in text
    assert f"form__id__exact={form.pk}" in text


def test_telegram_message_fields_are_grouped_by_form(admin_client):
    make_lead()
    Form.objects.update(questions=[{"key": "e-mail", "label": "Email"}])
    destination = make_destination(kind="telegram", url="", telegram_chat_id="-100")
    content = admin_client.get(
        reverse("admin:destinations_destination_change", args=[destination.pk])
    ).content.decode()
    form = Form.objects.get()
    assert f'<option value="{form.pk}">Test form</option>' in content
    assert f'data-form="{form.pk}"' in content
    assert 'data-insert="{e-mail}"' in content
    assert 'data-insert="{campaign}"' in content
