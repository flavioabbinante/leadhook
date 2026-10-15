from leadhook.core.models import Workspace, get_default_workspace


def test_default_workspace_is_created_by_migration():
    assert Workspace.objects.count() == 1
    assert Workspace.objects.get().name == "Default"


def test_get_default_workspace_returns_the_existing_one():
    assert get_default_workspace() == Workspace.objects.get()
