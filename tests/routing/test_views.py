import responses
from django.contrib.auth.models import User
from django.contrib.messages import get_messages

from leadhook.destinations.models import Delivery
from leadhook.leads.models import Lead
from tests.factories import make_destination, make_form, make_lead

ROUTING = "/admin/routing/"


def _messages(response) -> str:
    return " ".join(str(m) for m in get_messages(response.wsgi_request))


def test_routing_page_lists_pages_forms_and_destinations(admin_client):
    form = make_form()
    destination = make_destination(name="CRM intake")
    form.page.default_destinations.add(destination)
    html = admin_client.get(ROUTING).content.decode()
    assert "Nuova connessione" in html
    assert f'value="form:{form.pk}"' in html
    assert f'value="page:{form.page.pk}"' in html
    assert "CRM intake" in html
    assert "Predefinito della pagina:" in html  # the form inherits the page default


def test_connect_form_to_destination(admin_client):
    form = make_form()
    destination = make_destination()
    response = admin_client.post(
        ROUTING,
        {"action": "connect", "source": f"form:{form.pk}", "destination": destination.pk},
        follow=True,
    )
    assert list(form.destinations.all()) == [destination]
    assert "ora vanno a" in _messages(response)


def test_connect_page_default_and_disconnect(admin_client):
    form = make_form()
    destination = make_destination()
    source = f"page:{form.page.pk}"
    admin_client.post(
        ROUTING, {"action": "connect", "source": source, "destination": destination.pk}
    )
    assert list(form.page.default_destinations.all()) == [destination]
    admin_client.post(
        ROUTING, {"action": "disconnect", "source": source, "destination": destination.pk}
    )
    assert not form.page.default_destinations.exists()


def test_connecting_points_to_waiting_leads_and_deliver_now_routes_them(admin_client, mailoutbox):
    lead = make_lead(status=Lead.Status.UNROUTED)
    destination = make_destination()
    source = f"page:{lead.page.pk}"
    response = admin_client.post(
        ROUTING, {"action": "connect", "source": source, "destination": destination.pk}, follow=True
    )
    assert "erano in attesa di una destinazione" in _messages(response)
    with responses.RequestsMock() as mock:
        mock.post(destination.url, json={})
        admin_client.post(ROUTING, {"action": "deliver_waiting", "source": source})
    lead.refresh_from_db()
    assert lead.status == Lead.Status.DELIVERED
    assert Delivery.objects.get(lead=lead).status == Delivery.Status.SUCCESS


def test_invalid_source_is_rejected(admin_client):
    destination = make_destination()
    response = admin_client.post(
        ROUTING,
        {"action": "connect", "source": "page:999", "destination": destination.pk},
        follow=True,
    )
    assert "Scegli da dove arrivano i lead" in _messages(response)


def test_staff_without_change_permission_cannot_connect(client):
    form = make_form()
    destination = make_destination()
    user = User.objects.create_user("viewer", password="pw", is_staff=True)
    client.force_login(user)
    response = client.post(
        ROUTING, {"action": "connect", "source": f"form:{form.pk}", "destination": destination.pk}
    )
    assert response.status_code == 403
    assert not form.destinations.exists()
