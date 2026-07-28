# Siembra la plantilla de encuesta de satisfacción (ES/EN) y el catálogo inicial
# de motivos de voto negativo.
#
# El subject lleva [Ticket #{{ticket_id}}] por lo mismo que documenta 0042: si el
# cliente responde al correo en lugar de pulsar el enlace, ese token es lo único que
# permite a email_ingestion devolver la respuesta al ticket en vez de abrir otro.

from django.db import migrations

TEMPLATES = [
    {
        "key": "satisfaction_survey",
        "language": "es",
        "subject": "[Ticket #{{ticket_id}}] ¿Cómo valoras la atención recibida?",
        "body_html": (
            "<p>Hola {{requester_name}},</p>"
            "<p>Hemos dado por resuelta tu solicitud <strong>#{{ticket_id}}</strong>:</p>"
            "<blockquote>{{subject}}</blockquote>"
            "<p>¿Cómo valoras la atención que has recibido?</p>"
            "<p>"
            "<a href=\"{{survey_url_good}}\" style=\"display:inline-block;padding:10px 18px;"
            "margin-right:8px;background:#16a34a;color:#fff;border-radius:6px;"
            "text-decoration:none;\">Bien, estoy satisfecho</a>"
            "<a href=\"{{survey_url_bad}}\" style=\"display:inline-block;padding:10px 18px;"
            "background:#dc2626;color:#fff;border-radius:6px;text-decoration:none;\">"
            "Mal, no estoy satisfecho</a>"
            "</p>"
            "<p>Solo te llevará un momento y nos ayuda a mejorar. Si prefieres contarnos "
            "algo más, puedes responder directamente a este correo.</p>"
            "<p>Un saludo,<br>{{brand_name}}</p>"
        ),
        "body_text": (
            "Hola {{requester_name}},\n\n"
            "Hemos dado por resuelta tu solicitud #{{ticket_id}}:\n\n"
            "  {{subject}}\n\n"
            "¿Cómo valoras la atención que has recibido?\n\n"
            "  Bien, estoy satisfecho: {{survey_url_good}}\n"
            "  Mal, no estoy satisfecho: {{survey_url_bad}}\n\n"
            "Solo te llevará un momento y nos ayuda a mejorar. Si prefieres contarnos "
            "algo más, puedes responder directamente a este correo.\n\n"
            "Un saludo,\n{{brand_name}}"
        ),
    },
    {
        "key": "satisfaction_survey",
        "language": "en",
        "subject": "[Ticket #{{ticket_id}}] How would you rate the support you received?",
        "body_html": (
            "<p>Hello {{requester_name}},</p>"
            "<p>We have marked your request <strong>#{{ticket_id}}</strong> as resolved:</p>"
            "<blockquote>{{subject}}</blockquote>"
            "<p>How would you rate the support you received?</p>"
            "<p>"
            "<a href=\"{{survey_url_good}}\" style=\"display:inline-block;padding:10px 18px;"
            "margin-right:8px;background:#16a34a;color:#fff;border-radius:6px;"
            "text-decoration:none;\">Good, I'm satisfied</a>"
            "<a href=\"{{survey_url_bad}}\" style=\"display:inline-block;padding:10px 18px;"
            "background:#dc2626;color:#fff;border-radius:6px;text-decoration:none;\">"
            "Bad, I'm unsatisfied</a>"
            "</p>"
            "<p>It only takes a moment and helps us improve. If you would rather tell us "
            "more, just reply to this email.</p>"
            "<p>Best regards,<br>{{brand_name}}</p>"
        ),
        "body_text": (
            "Hello {{requester_name}},\n\n"
            "We have marked your request #{{ticket_id}} as resolved:\n\n"
            "  {{subject}}\n\n"
            "How would you rate the support you received?\n\n"
            "  Good, I'm satisfied: {{survey_url_good}}\n"
            "  Bad, I'm unsatisfied: {{survey_url_bad}}\n\n"
            "It only takes a moment and helps us improve. If you would rather tell us "
            "more, just reply to this email.\n\n"
            "Best regards,\n{{brand_name}}"
        ),
    },
]

# Catálogo inicial de motivos, en la línea de los que ofrecía Zendesk.
REASONS = [
    ("slow_resolution", 10, "Ha tardado demasiado en resolverse", "It took too long to resolve"),
    ("not_resolved", 20, "El problema no se ha resuelto", "The issue was not resolved"),
    ("no_answer", 30, "No se ha respondido a mi pregunta", "My question was not answered"),
    ("communication", 40, "La comunicación no ha sido buena", "Communication was poor"),
    ("other", 90, "Otro motivo", "Other reason"),
]


def seed_forward(apps, schema_editor):
    ResponseTemplate = apps.get_model("app", "ResponseTemplate")
    for tpl in TEMPLATES:
        ResponseTemplate.objects.get_or_create(
            key=tpl["key"], brand=None, language=tpl["language"],
            defaults={
                "subject": tpl["subject"],
                "body_html": tpl["body_html"],
                "body_text": tpl["body_text"],
                "active": True,
            },
        )

    SatisfactionReason = apps.get_model("app", "SatisfactionReason")
    for code, position, label_es, label_en in REASONS:
        SatisfactionReason.objects.get_or_create(
            code=code, language="es",
            defaults={"label": label_es, "position": position, "active": True},
        )
        SatisfactionReason.objects.get_or_create(
            code=code, language="en",
            defaults={"label": label_en, "position": position, "active": True},
        )


def seed_reverse(apps, schema_editor):
    ResponseTemplate = apps.get_model("app", "ResponseTemplate")
    ResponseTemplate.objects.filter(
        key="satisfaction_survey", brand__isnull=True, language__in=["es", "en"]
    ).delete()
    SatisfactionReason = apps.get_model("app", "SatisfactionReason")
    SatisfactionReason.objects.filter(code__in=[code for code, _, _, _ in REASONS]).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0046_satisfaction_native"),
    ]

    operations = [
        migrations.RunPython(seed_forward, seed_reverse),
    ]
