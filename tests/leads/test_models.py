import pytest
from django.db import IntegrityError

from leadhook.leads.models import Lead
from tests.factories import make_lead


def test_leadgen_id_is_unique():
    lead = make_lead()
    with pytest.raises(IntegrityError):
        make_lead(form=lead.form)


def test_new_lead_defaults():
    lead = Lead.objects.create(leadgen_id="999", page_external_id="111", form_external_id="222")
    assert lead.status == Lead.Status.RECEIVED
    assert lead.source == Lead.Source.WEBHOOK
    assert lead.data == {}
    assert lead.field_data == []
