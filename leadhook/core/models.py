from django.db import models


class Workspace(models.Model):
    name = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self) -> str:
        return self.name


def get_default_workspace() -> Workspace:
    workspace = Workspace.objects.order_by("id").first()
    if workspace is None:
        workspace = Workspace.objects.create(name="Default")
    return workspace
