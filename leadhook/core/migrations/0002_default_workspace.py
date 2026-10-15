from django.db import migrations


def create_default_workspace(apps, schema_editor):
    Workspace = apps.get_model("core", "Workspace")
    if not Workspace.objects.exists():
        Workspace.objects.create(name="Default")


class Migration(migrations.Migration):
    dependencies = [("core", "0001_initial")]

    operations = [migrations.RunPython(create_default_workspace, migrations.RunPython.noop)]
