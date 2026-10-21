from django.contrib.auth.models import Group, Permission, User
from django.urls import reverse

from tests.factories import make_lead


def _staff_with_all_lead_permissions():
    group = Group.objects.create(name="Operatori")
    group.permissions.set(Permission.objects.filter(content_type__app_label="leads"))
    user = User.objects.create_user("operatore", password="pw", is_staff=True)
    user.groups.add(group)
    return user


def test_only_superusers_can_delete():
    user = _staff_with_all_lead_permissions()
    assert user.has_perm("leads.view_lead")
    assert user.has_perm("leads.change_lead")
    assert not user.has_perm("leads.delete_lead")
    assert User.objects.create_superuser("boss", password="pw").has_perm("leads.delete_lead")


def test_admin_hides_delete_for_non_superusers(client):
    lead = make_lead()
    client.force_login(_staff_with_all_lead_permissions())
    changelist = client.get(reverse("admin:leads_lead_changelist")).content.decode()
    assert 'value="delete_selected"' not in changelist
    delete_url = reverse("admin:leads_lead_delete", args=[lead.pk])
    assert client.post(delete_url, {"post": "yes"}).status_code == 403
