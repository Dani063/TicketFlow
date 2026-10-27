from django.db import migrations


PRODUCTS = (
    ('cognitia', 'Cognitia', '#8b5cf6', 'fa-brain', 52),
    ('rightfax', 'Rightfax', '#0f766e', 'fa-fax', 54),
)


def seed_products(apps, schema_editor):
    products = apps.get_model('app', 'ProductLine').objects.using(schema_editor.connection.alias)
    for code, name, color, icon, order in PRODUCTS:
        products.update_or_create(code=code, defaults={
            'name': name, 'color': color, 'icon': icon, 'sort_order': order, 'active': True,
        })


def unseed_products(apps, schema_editor):
    apps.get_model('app', 'ProductLine').objects.using(schema_editor.connection.alias).filter(
        code__in=[row[0] for row in PRODUCTS],
    ).delete()


class Migration(migrations.Migration):
    dependencies = [('app', '0067_inbound_ids_case_sensitive')]
    operations = [migrations.RunPython(seed_products, unseed_products)]
