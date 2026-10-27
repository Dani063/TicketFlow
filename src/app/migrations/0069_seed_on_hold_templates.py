"""Plantillas y datos del estado on_hold (#21384).

- Siembra el aviso suelto de cierre (``ticket_on_hold``, ES/EN).
- Añade el aviso a las respuestas de agente sembradas en la 0056, solo si nadie
  las ha editado; una plantilla editada lo recibe al final al renderizar.
- Las macros que cerraban tickets pasan a on_hold: 'closed' ya solo lo pone el
  sistema.
"""

from django.db import migrations


ON_HOLD_TEMPLATES = [
    {
        "language": "es",
        "subject": "[Ticket #{{ticket_id}}] Solicitud resuelta: {{subject}}",
        "body_html": (
            "<p>Hola {{requester_name}},</p>"
            "<p>Te escribimos sobre tu solicitud <strong>#{{ticket_id}}</strong>.</p>"
            "{{closing_notice_html|safe}}"
            "<p>Un saludo,<br>{{brand_name}}</p>"
        ),
        "body_text": (
            "Hola {{requester_name}},\n\n"
            "Te escribimos sobre tu solicitud #{{ticket_id}}.\n\n"
            "{{closing_notice_text}}\n\n"
            "Un saludo,\n{{brand_name}}"
        ),
    },
    {
        "language": "en",
        "subject": "[Ticket #{{ticket_id}}] Request resolved: {{subject}}",
        "body_html": (
            "<p>Hello {{requester_name}},</p>"
            "<p>We are writing about your request <strong>#{{ticket_id}}</strong>.</p>"
            "{{closing_notice_html|safe}}"
            "<p>Best regards,<br>{{brand_name}}</p>"
        ),
        "body_text": (
            "Hello {{requester_name}},\n\n"
            "We are writing about your request #{{ticket_id}}.\n\n"
            "{{closing_notice_text}}\n\n"
            "Best regards,\n{{brand_name}}"
        ),
    },
]

# Respuesta de agente tal y como la sembró la 0056 → con el aviso de cierre.
AGENT_REPLY_NOTICE = {
    "es": (
        "<blockquote>{{reply_html|safe}}</blockquote>",
        "<blockquote>{{reply_html|safe}}</blockquote>{{closing_notice_html|safe}}",
        "{{reply_text}}\n\n",
        "{{reply_text}}\n\n{% if closing_notice_text %}{{closing_notice_text}}\n\n{% endif %}",
    ),
    "en": (
        "<blockquote>{{reply_html|safe}}</blockquote>",
        "<blockquote>{{reply_html|safe}}</blockquote>{{closing_notice_html|safe}}",
        "{{reply_text}}\n\n",
        "{{reply_text}}\n\n{% if closing_notice_text %}{{closing_notice_text}}\n\n{% endif %}",
    ),
}
SEEDED_AGENT_REPLY_HTML = {
    "es": (
        "<p>Hola {{requester_name}},</p>"
        "<p>{{agent_name}} ha respondido a tu solicitud "
        "<strong>#{{ticket_id}}</strong>:</p>"
        "<blockquote>{{reply_html|safe}}</blockquote>"
        "<p>Puedes continuar la conversación respondiendo directamente a este correo.</p>"
        "<p>Un saludo,<br>{{brand_name}}</p>"
    ),
    "en": (
        "<p>Hello {{requester_name}},</p>"
        "<p>{{agent_name}} replied to your request "
        "<strong>#{{ticket_id}}</strong>:</p>"
        "<blockquote>{{reply_html|safe}}</blockquote>"
        "<p>You can continue the conversation by replying directly to this email.</p>"
        "<p>Best regards,<br>{{brand_name}}</p>"
    ),
}


def _seeded_agent_replies(ResponseTemplate, language, body_html):
    return ResponseTemplate.objects.filter(
        key="agent_reply", brand__isnull=True, language=language, body_html=body_html,
    )


def forward(apps, schema_editor):
    ResponseTemplate = apps.get_model("app", "ResponseTemplate")
    Macro = apps.get_model("app", "Macro")

    for template in ON_HOLD_TEMPLATES:
        ResponseTemplate.objects.get_or_create(
            key="ticket_on_hold",
            brand=None,
            language=template["language"],
            defaults={
                "subject": template["subject"],
                "body_html": template["body_html"],
                "body_text": template["body_text"],
                "active": True,
            },
        )

    for language, (html_old, html_new, text_old, text_new) in AGENT_REPLY_NOTICE.items():
        for template in _seeded_agent_replies(ResponseTemplate, language, SEEDED_AGENT_REPLY_HTML[language]):
            template.body_html = template.body_html.replace(html_old, html_new, 1)
            template.body_text = template.body_text.replace(text_old, text_new, 1)
            template.save(update_fields=["body_html", "body_text"])

    for macro in Macro.objects.all():
        actions = macro.actions or {}
        if isinstance(actions, dict) and actions.get("status") == "closed":
            macro.actions = {**actions, "status": "on_hold"}
            macro.save(update_fields=["actions"])


def reverse(apps, schema_editor):
    ResponseTemplate = apps.get_model("app", "ResponseTemplate")
    Macro = apps.get_model("app", "Macro")

    ResponseTemplate.objects.filter(
        key="ticket_on_hold", brand__isnull=True, language__in=["es", "en"],
    ).delete()

    for language, (html_old, html_new, text_old, text_new) in AGENT_REPLY_NOTICE.items():
        updated_html = SEEDED_AGENT_REPLY_HTML[language].replace(html_old, html_new, 1)
        for template in _seeded_agent_replies(ResponseTemplate, language, updated_html):
            template.body_html = template.body_html.replace(html_new, html_old, 1)
            template.body_text = template.body_text.replace(text_new, text_old, 1)
            template.save(update_fields=["body_html", "body_text"])

    for macro in Macro.objects.all():
        actions = macro.actions or {}
        if isinstance(actions, dict) and actions.get("status") == "on_hold":
            macro.actions = {**actions, "status": "closed"}
            macro.save(update_fields=["actions"])


class Migration(migrations.Migration):
    dependencies = [
        ("app", "0068_ticket_on_hold"),
    ]

    operations = [
        migrations.RunPython(forward, reverse),
    ]
