import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, connection

from leadhook.destinations.models import Delivery, Destination
from tests.factories import make_destination, make_lead


def test_one_delivery_per_lead_and_destination():
    lead = make_lead()
    destination = make_destination()
    Delivery.objects.create(lead=lead, destination=destination)
    with pytest.raises(IntegrityError):
        Delivery.objects.create(lead=lead, destination=destination)


def test_delivery_public_id():
    delivery = Delivery.objects.create(lead=make_lead(), destination=make_destination())
    assert delivery.public_id == f"dlv_{delivery.pk}"
    assert Delivery().public_id == "dlv_preview"


def test_destination_secret_is_encrypted_at_rest():
    destination = make_destination(auth_type=Destination.AuthType.BEARER, secret="s3cret-value")
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT secret FROM destinations_destination WHERE id = %s", [destination.pk]
        )
        assert cursor.fetchone()[0] != "s3cret-value"


def test_page_and_form_destination_links():
    lead = make_lead()
    destination = make_destination()
    lead.page.default_destinations.add(destination)
    lead.form.destinations.add(destination)
    assert list(destination.default_for_pages.all()) == [lead.page]
    assert list(destination.forms.all()) == [lead.form]


@pytest.mark.parametrize(
    ("auth_type", "header_name", "secret", "error_field"),
    [
        (Destination.AuthType.HEADER, "", "x", "auth_header_name"),
        (Destination.AuthType.HEADER, "X-Api-Key", "", "secret"),
        (Destination.AuthType.BEARER, "", "", "secret"),
        (Destination.AuthType.HMAC, "", "", "secret"),
    ],
)
def test_destination_clean_requires_auth_details(auth_type, header_name, secret, error_field):
    destination = Destination(
        name="x",
        url="https://a.example.com",
        auth_type=auth_type,
        auth_header_name=header_name,
        secret=secret,
    )
    with pytest.raises(ValidationError) as excinfo:
        destination.clean()
    assert error_field in excinfo.value.message_dict


def test_destination_clean_rejects_non_object_extra_headers():
    destination = Destination(name="x", url="https://a.example.com", extra_headers=["nope"])
    with pytest.raises(ValidationError) as excinfo:
        destination.clean()
    assert "extra_headers" in excinfo.value.message_dict


@pytest.mark.parametrize(
    ("changes", "error_field"),
    [
        ({"extra_headers": {"X-Name": "caf€"}}, "extra_headers"),
        ({"extra_headers": {"X-Näme€": "v"}}, "extra_headers"),
        ({"extra_headers": {"X-Name": "a\r\nInjected: 1"}}, "extra_headers"),
        ({"extra_headers": {"X-Na\nme": "v"}}, "extra_headers"),
        ({"extra_headers": {"": "v"}}, "extra_headers"),
        (
            {"auth_type": Destination.AuthType.HEADER, "auth_header_name": "X-K€", "secret": "s"},
            "auth_header_name",
        ),
        (
            {
                "auth_type": Destination.AuthType.HEADER,
                "auth_header_name": "X-K\r\n",
                "secret": "s",
            },
            "auth_header_name",
        ),
        ({"auth_type": Destination.AuthType.BEARER, "secret": "t€ken"}, "secret"),
        ({"auth_type": Destination.AuthType.BEARER, "secret": "tok\nen"}, "secret"),
    ],
)
def test_destination_clean_rejects_unsendable_headers(changes, error_field):
    destination = Destination(name="x", url="https://a.example.com", **changes)
    with pytest.raises(ValidationError) as excinfo:
        destination.clean()
    assert error_field in excinfo.value.message_dict
    assert "€" not in str(excinfo.value.message_dict.get("secret", ""))


def test_destination_clean_accepts_latin1_headers():
    destination = Destination(
        name="x",
        url="https://a.example.com",
        auth_type=Destination.AuthType.HEADER,
        auth_header_name="X-Key",
        secret="sécret",
        extra_headers={"X-Source": "café", "X-Count": 3},
    )
    destination.clean()


@pytest.mark.parametrize("timeout", [0, 121])
def test_destination_timeout_must_be_between_1_and_120(timeout):
    destination = make_destination(timeout_s=timeout)
    with pytest.raises(ValidationError) as excinfo:
        destination.full_clean()
    assert "timeout_s" in excinfo.value.message_dict
