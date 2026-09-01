from django.db import migrations


ALIASES = {
    "recordia": "recordia",
    "speech analytics": "speech-analytics",
    "speech_analytics": "speech-analytics",
    "speech-analytics": "speech-analytics",
    "identia": "identia",
    "ecomfax": "ecomfax",
    "ecomfaxpro": "ecomfax",
    "agentia365": "agentia365",
    "agentia_365": "agentia365",
    "agentia-365": "agentia365",
}


def backfill_product_line(apps, schema_editor):
    ProductLine = apps.get_model("app", "ProductLine")
    Ticket = apps.get_model("app", "Ticket")
    ids = dict(ProductLine.objects.values_list("code", "id"))
    for ticket in Ticket.objects.filter(product_line__isnull=True).exclude(service__isnull=True).exclude(service="").iterator():
        normalized = ticket.service.strip().lower()
        code = ALIASES.get(normalized, "otros-servicios")
        product_id = ids.get(code)
        if product_id:
            Ticket.objects.filter(pk=ticket.pk, product_line__isnull=True).update(product_line_id=product_id)


class Migration(migrations.Migration):
    dependencies = [("app", "0054_product_line_reporting")]

    operations = [migrations.RunPython(backfill_product_line, migrations.RunPython.noop)]
