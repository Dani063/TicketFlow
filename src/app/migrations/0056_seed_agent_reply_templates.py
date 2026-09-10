"""Siembra las plantillas globales de respuesta pública del agente (ES/EN)."""

from django.db import migrations


TEMPLATES = [
    {
        "language": "es",
        "subject": "[Ticket #{{ticket_id}}] Respuesta de soporte: {{subject}}",
        "body_html": (
            "<p>Hola {{requester_name}},</p>"
            "<p>{{agent_name}} ha respondido a tu solicitud "
            "<strong>#{{ticket_id}}</strong>:</p>"
            "<blockquote>{{reply_html|safe}}</blockquote>"
            "<p>Puedes continuar la conversación respondiendo directamente a este correo.</p>"
            "<p>Un saludo,<br>{{brand_name}}</p>"
        ),
        "body_text": (
            "Hola {{requester_name}},\n\n"
            "{{agent_name}} ha respondido a tu solicitud #{{ticket_id}}:\n\n"
            "{{reply_text}}\n\n"
            "Puedes continuar la conversación respondiendo directamente a este correo.\n\n"
            "Un saludo,\n{{brand_name}}"
        ),
    },
    {
        "language": "en",
        "subject": "[Ticket #{{ticket_id}}] Support reply: {{subject}}",
        "body_html": (
            "<p>Hello {{requester_name}},</p>"
            "<p>{{agent_name}} replied to your request "
            "<strong>#{{ticket_id}}</strong>:</p>"
            "<blockquote>{{reply_html|safe}}</blockquote>"
            "<p>You can continue the conversation by replying directly to this email.</p>"
            "<p>Best regards,<br>{{brand_name}}</p>"
        ),
        "body_text": (
            "Hello {{requester_name}},\n\n"
            "{{agent_name}} replied to your request #{{ticket_id}}:\n\n"
            "{{reply_text}}\n\n"
            "You can continue the conversation by replying directly to this email.\n\n"
            "Best regards,\n{{brand_name}}"
        ),
    },
]


def seed_forward(apps, schema_editor):
    ResponseTemplate = apps.get_model("app", "ResponseTemplate")
    for template in TEMPLATES:
        ResponseTemplate.objects.get_or_create(
            key="agent_reply",
            brand=None,
            language=template["language"],
            defaults={
                "subject": template["subject"],
                "body_html": template["body_html"],
                "body_text": template["body_text"],
                "active": True,
            },
        )


def seed_reverse(apps, schema_editor):
    ResponseTemplate = apps.get_model("app", "ResponseTemplate")
    ResponseTemplate.objects.filter(
        key="agent_reply",
        brand__isnull=True,
        language__in=["es", "en"],
    ).delete()


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0055_backfill_product_line"),
    ]

    operations = [
        migrations.RunPython(seed_forward, seed_reverse),
    ]
