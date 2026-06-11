# Siembra las plantillas globales de confirmación de creación de ticket (ES/EN).
#
# El subject DEBE contener [Ticket #{{ticket_id}}]: las respuestas del cliente a la
# confirmación llegan con un conversationId nuevo de Graph, así que el regex de
# email_ingestion sobre ese token es lo único que las enlaza de vuelta al ticket.

from django.db import migrations

TEMPLATES = [
    {
        "key": "ticket_created",
        "language": "es",
        "subject": "[Ticket #{{ticket_id}}] Hemos recibido tu solicitud: {{subject}}",
        "body_html": (
            "<p>Hola {{requester_name}},</p>"
            "<p>Hemos recibido tu solicitud y la hemos registrado con el número "
            "<strong>#{{ticket_id}}</strong>:</p>"
            "<blockquote>{{subject}}</blockquote>"
            "<p>Nuestro equipo la revisará lo antes posible. Puedes añadir información "
            "adicional simplemente respondiendo a este correo.</p>"
            "<p>Un saludo,<br>{{brand_name}}</p>"
        ),
        "body_text": (
            "Hola {{requester_name}},\n\n"
            "Hemos recibido tu solicitud y la hemos registrado con el número #{{ticket_id}}:\n\n"
            "  {{subject}}\n\n"
            "Nuestro equipo la revisará lo antes posible. Puedes añadir información "
            "adicional simplemente respondiendo a este correo.\n\n"
            "Un saludo,\n{{brand_name}}"
        ),
    },
    {
        "key": "ticket_created",
        "language": "en",
        "subject": "[Ticket #{{ticket_id}}] We received your request: {{subject}}",
        "body_html": (
            "<p>Hello {{requester_name}},</p>"
            "<p>We have received your request and registered it with number "
            "<strong>#{{ticket_id}}</strong>:</p>"
            "<blockquote>{{subject}}</blockquote>"
            "<p>Our team will review it as soon as possible. You can add more details "
            "by simply replying to this email.</p>"
            "<p>Best regards,<br>{{brand_name}}</p>"
        ),
        "body_text": (
            "Hello {{requester_name}},\n\n"
            "We have received your request and registered it with number #{{ticket_id}}:\n\n"
            "  {{subject}}\n\n"
            "Our team will review it as soon as possible. You can add more details "
            "by simply replying to this email.\n\n"
            "Best regards,\n{{brand_name}}"
        ),
    },
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


def seed_reverse(apps, schema_editor):
    ResponseTemplate = apps.get_model("app", "ResponseTemplate")
    ResponseTemplate.objects.filter(
        key="ticket_created", brand__isnull=True, language__in=["es", "en"]
    ).delete()


class Migration(migrations.Migration):

    dependencies = [
        ("app", "0041_response_templates_outbound_email"),
    ]

    operations = [
        migrations.RunPython(seed_forward, seed_reverse),
    ]
