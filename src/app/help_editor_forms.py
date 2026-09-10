from django import forms
from django.utils.text import slugify

from app.models import HelpSection
from app.services.help_content import help_plain_text, sanitize_help_html


class HelpArticleEditorForm(forms.Form):
    section = forms.ModelChoiceField(queryset=HelpSection.objects.none(), label="Sección")
    title = forms.CharField(max_length=500, label="Título")
    slug = forms.SlugField(max_length=220, required=False, label="URL del artículo")
    body_html = forms.CharField(widget=forms.Textarea, label="Contenido")
    promoted = forms.BooleanField(required=False, label="Artículo destacado")
    position = forms.IntegerField(min_value=0, required=False, initial=0, label="Posición")
    change_note = forms.CharField(max_length=500, required=False, label="Resumen del cambio")
    expected_version = forms.IntegerField(min_value=0, widget=forms.HiddenInput)

    def __init__(self, *args, center, locale, article=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.center = center
        self.locale = locale
        self.article = article
        self.fields["section"].queryset = HelpSection.objects.filter(
            category__center=center,
            category__locale=locale,
            category__published=True,
            published=True,
        ).select_related("category").order_by("category__position", "position", "name")
        self.fields["section"].label_from_instance = lambda obj: f"{obj.category.name} · {obj.name}"

    def clean_title(self):
        return self.cleaned_data["title"].strip()

    def clean_slug(self):
        value = slugify(self.cleaned_data.get("slug") or "", allow_unicode=False)
        return value[:220]

    def clean_body_html(self):
        body = sanitize_help_html(self.cleaned_data.get("body_html", ""))
        if not help_plain_text(body) and "<img" not in body.lower() and "<video" not in body.lower():
            raise forms.ValidationError("Añade contenido al artículo.")
        return body

    def clean_section(self):
        section = self.cleaned_data["section"]
        if section.category.center_id != self.center.id or section.category.locale != self.locale:
            raise forms.ValidationError("La sección no pertenece al centro de ayuda seleccionado.")
        return section
