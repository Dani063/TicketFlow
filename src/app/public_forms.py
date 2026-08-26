import time

from django import forms
from django.conf import settings
from django.core import signing


COPY = {
    "es": {
        "email": "Correo electrónico",
        "subject": "Asunto",
        "description": "Descripción",
        "phone": "Teléfono",
        "attachments": "Archivos adjuntos (opcional)",
        "required": "Este campo es obligatorio.",
        "invalid_email": "Introduce un correo electrónico válido.",
        "too_fast": "No hemos podido validar el formulario. Recarga la página e inténtalo de nuevo.",
    },
    "en": {
        "email": "Email address",
        "subject": "Subject",
        "description": "Description",
        "phone": "Phone number",
        "attachments": "Attachments (optional)",
        "required": "This field is required.",
        "invalid_email": "Enter a valid email address.",
        "too_fast": "We could not validate the form. Reload the page and try again.",
    },
}


class PublicTicketForm(forms.Form):
    email = forms.EmailField(max_length=254)
    subject = forms.CharField(max_length=255)
    description = forms.CharField(max_length=20000, widget=forms.Textarea(attrs={"rows": 8}))
    phone = forms.CharField(max_length=64)
    # Campo trampa: debe permanecer vacio y oculto visualmente.
    website = forms.CharField(required=False)
    started = forms.CharField(widget=forms.HiddenInput)

    def __init__(self, *args, locale="es", **kwargs):
        super().__init__(*args, **kwargs)
        self.locale = locale if locale in COPY else "es"
        copy = COPY[self.locale]
        for name in ("email", "subject", "description", "phone"):
            self.fields[name].label = copy[name]
            self.fields[name].error_messages["required"] = copy["required"]
        self.fields["email"].error_messages["invalid"] = copy["invalid_email"]
        self.fields["subject"].widget.attrs.update({"autocomplete": "off"})
        self.fields["email"].widget.attrs.update({"autocomplete": "email"})
        self.fields["phone"].widget.attrs.update({"autocomplete": "tel"})
        self.fields["description"].widget.attrs.update({"spellcheck": "true"})

    @staticmethod
    def new_started_token():
        return signing.TimestampSigner(salt="public-ticket-form").sign(str(int(time.time())))

    def clean_started(self):
        token = self.cleaned_data.get("started", "")
        try:
            raw = signing.TimestampSigner(salt="public-ticket-form").unsign(token, max_age=86400)
            started_at = int(raw)
        except (signing.BadSignature, signing.SignatureExpired, TypeError, ValueError):
            raise forms.ValidationError(COPY[self.locale]["too_fast"])
        if time.time() - started_at < getattr(settings, "PUBLIC_FORM_MIN_SECONDS", 2):
            raise forms.ValidationError(COPY[self.locale]["too_fast"])
        return token

    def clean_website(self):
        if (self.cleaned_data.get("website") or "").strip():
            raise forms.ValidationError(COPY[self.locale]["too_fast"])
        return ""
