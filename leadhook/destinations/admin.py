import json
from functools import partial

from django import forms
from django.contrib import admin
from django.db.models import Q
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.html import format_html, format_html_join

from leadhook.core.admin_forms import SecretAwareModelForm
from leadhook.core.models import get_default_workspace
from leadhook.leads.models import Lead
from leadhook.meta.models import Form

from .models import Delivery, Destination, FieldMapping
from .payload import build_context, render_body
from .services import reset_for_resend
from .tasks import deliver
from .telegram import LEAD_PLACEHOLDERS
from .templating import TemplateError


class DestinationForm(SecretAwareModelForm):
    secret_fields = ("secret",)

    class Meta:
        model = Destination
        exclude = ("workspace",)
        help_texts = {
            "extra_headers": (
                'Header aggiuntivi come oggetto JSON, es. {"X-Source": "leadhook"}. '
                "Salvati in chiaro: metti le credenziali in Chiave segreta, non qui."
            ),
        }


def routed_forms(destination: Destination | None):
    """Forms with questions routed to the destination, or every such form while none is."""
    forms_with_questions = Form.objects.exclude(questions=[]).order_by("name")
    if destination is not None:
        # Same rule as routing: a form's own destinations replace its page's defaults.
        routed = forms_with_questions.filter(
            Q(destinations=destination)
            | Q(page__default_destinations=destination, destinations=None)
        ).distinct()
        if routed.exists():
            return routed
    return forms_with_questions


def routed_questions(destination: Destination | None) -> dict[str, str]:
    """{key: label} of the questions of the routed forms."""
    questions: dict[str, str] = {}
    for form in routed_forms(destination).only("questions"):
        for question in form.questions:
            questions.setdefault(question["key"], question["label"])
    return questions


def source_choices(destination: Destination | None) -> list:
    """Dropdown of the Value column: form questions, lead details and a fixed value."""
    questions = routed_questions(destination)
    answers = [(f"answer:{key}", f"{label} ({key})") for key, label in sorted(questions.items())]
    if destination is not None:
        # Keep saved answers selectable even when no routed form asks them any more.
        for source in destination.field_mappings.values_list("source", flat=True):
            key = source.removeprefix("answer:")
            if source.startswith("answer:") and key not in questions:
                questions[key] = key
                answers.append((source, f"{key} (non presente nei form collegati)"))
    return [
        ("", "---------"),
        ("Risposte del form", answers),
        (
            "Dati del lead",
            [(f"lead:{path}", label) for path, label in FieldMapping.LEAD_SOURCES.items()],
        ),
        ("Altro", [(FieldMapping.FIXED, "Valore fisso")]),
    ]


class FieldMappingInline(admin.TabularInline):
    model = FieldMapping
    extra = 3
    verbose_name = "campo"
    verbose_name_plural = "Campi da inviare"

    def get_formset(self, request, obj=None, **kwargs):
        kwargs["formfield_callback"] = partial(
            self.formfield_for_dbfield, request=request, destination=obj
        )
        return super().get_formset(request, obj, **kwargs)

    def formfield_for_dbfield(self, db_field, request, destination=None, **kwargs):
        if db_field.name == "source":
            return forms.ChoiceField(
                label=db_field.verbose_name, choices=source_choices(destination)
            )
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(Destination)
class DestinationAdmin(admin.ModelAdmin):
    form = DestinationForm
    list_display = ("name", "kind", "url", "active")
    list_filter = ("active", "kind")
    search_fields = ("name", "url")
    readonly_fields = ("payload_preview", "message_fields")
    inlines = (FieldMappingInline,)
    fieldsets = (
        (None, {"fields": ("name", "kind", "active")}),
        (
            "Webhook",
            {
                "classes": ("lh-kind-webhook",),
                "fields": ("url", "method", "auth_type", "auth_header_name"),
            },
        ),
        (
            "Telegram",
            {
                "classes": ("lh-kind-telegram",),
                "fields": ("telegram_chat_id", "message_template", "message_fields"),
                "description": (
                    "1. Crea un bot con @BotFather e incolla il suo token in Token del bot.<br>"
                    "2. Aggiungi il bot alla chat o al gruppo e mandagli un messaggio.<br>"
                    "3. Apri api.telegram.org/bot&lt;token&gt;/getUpdates e copia qui chat.id."
                ),
            },
        ),
        ("Chiave segreta", {"fields": ("secret",)}),
        ("Anteprima", {"fields": ("payload_preview",)}),
        (
            "Avanzate",
            {
                "classes": ("collapse", "lh-kind-webhook"),
                "fields": ("payload_template", "extra_headers", "success_condition", "timeout_s"),
            },
        ),
    )

    class Media:
        # Shows only the sections of the chosen kind and authentication.
        js = ("leadhook/destination_form.js",)

    def save_model(self, request, obj, form, change):
        if not change:
            obj.workspace = get_default_workspace()
        super().save_model(request, obj, form, change)

    def get_object(self, request, object_id, from_field=None):
        obj = super().get_object(request, object_id, from_field)
        if obj is not None:
            # Per request: the admin instance is shared between threads.
            obj._preview_lead_id = request.GET.get("preview_lead")
        return obj

    @admin.display(description="Inserisci un campo")
    def message_fields(self, obj: Destination):
        forms = list(routed_forms(obj if obj.pk else None))
        return render_to_string(
            "admin/leadhook_message_fields.html",
            {
                "forms": forms,
                "details": [(name, label) for name, (_, label) in LEAD_PLACEHOLDERS.items()],
            },
        )

    @admin.display(description="Anteprima del payload")
    def payload_preview(self, obj: Destination):
        if not obj.pk:
            return "Salva la destinazione per vedere l'anteprima."
        leads = Lead.objects.exclude(status=Lead.Status.RECEIVED).filter(purged_at__isnull=True)
        lead_id = getattr(obj, "_preview_lead_id", None)
        lead = leads.filter(leadgen_id=lead_id).first() if lead_id else leads.first()
        if lead is None:
            return "Nessun lead scaricato finora."
        context = build_context(
            lead, Delivery(lead=lead, destination=obj), attempt=1, sent_at=timezone.now()
        )
        try:
            body = json.dumps(json.loads(render_body(obj, context)), indent=2, ensure_ascii=False)
        except TemplateError as exc:
            return str(exc)
        others = format_html_join(
            " · ",
            '<a href="?preview_lead={}">{}</a>',
            ((other.leadgen_id, other.leadgen_id) for other in leads[:10]),
        )
        return format_html(
            "<p>Lead usato: {}. Vedi l'anteprima con un altro: {}</p><pre>{}</pre>",
            lead.leadgen_id,
            others,
            body,
        )


@admin.register(Delivery)
class DeliveryAdmin(admin.ModelAdmin):
    list_display = (
        "lead",
        "destination",
        "status",
        "attempts",
        "last_status_code",
        "next_retry_at",
        "updated_at",
    )
    list_filter = ("status", "destination")
    search_fields = ("lead__leadgen_id",)
    actions = ["resend"]
    readonly_fields = [f.name for f in Delivery._meta.fields]

    def has_add_permission(self, request):
        return False

    @admin.action(description="Reinvia le consegne selezionate")
    def resend(self, request, queryset):
        count = skipped = 0
        for delivery in queryset.select_related("lead"):
            # Pending deliveries are being sent or wait for a retry; resetting them would
            # break their lease, so only finished (success/failed) ones are resent.
            if reset_for_resend(delivery):
                deliver.delay(delivery.pk)
                count += 1
            else:
                skipped += 1
        message = f"{count} consegne messe in coda per il reinvio."
        if skipped:
            message += (
                f" {skipped} saltate (in corso, programmate o con i dati del lead eliminati)."
            )
        self.message_user(request, message)


class DeliveryInline(admin.TabularInline):
    model = Delivery
    extra = 0
    can_delete = False
    fields = ("destination", "status", "attempts", "last_status_code", "last_error", "delivered_at")
    readonly_fields = fields

    def has_add_permission(self, request, obj=None):
        return False
