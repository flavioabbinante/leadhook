from django import forms

from .crypto import mask_secret


class SecretAwareModelForm(forms.ModelForm):
    """Renders secret fields as empty password inputs; a blank submission keeps the stored value."""

    secret_fields: tuple[str, ...] = ()

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for name in self.secret_fields:
            field = self.fields.get(name)
            if field is None:
                continue
            field.widget = forms.PasswordInput(render_value=False)
            stored = getattr(self.instance, name, "") if self.instance.pk else ""
            if stored:
                field.required = False
                field.help_text = f"Stored: {mask_secret(stored)}. Leave blank to keep it."

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk:
            for name in self.secret_fields:
                if name in self.fields and not cleaned.get(name):
                    cleaned[name] = getattr(self.instance, name)
        return cleaned
