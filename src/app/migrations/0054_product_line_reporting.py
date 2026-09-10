from django.db import migrations, models
import django.db.models.deletion


PRODUCTS = (
    ("recordia", "Recordia", "#4f6bed", "fa-wave-square", 10),
    ("speech-analytics", "Speech Analytics", "#0891b2", "fa-chart-line", 20),
    ("identia", "Identia", "#7c3aed", "fa-fingerprint", 30),
    ("ecomfax", "eComFax", "#ea7c2b", "fa-fax", 40),
    ("agentia365", "Agentia365", "#3a9b66", "fa-headset", 50),
    ("otros-servicios", "Otros servicios", "#64748b", "fa-cubes", 60),
)


def seed_products(apps, schema_editor):
    ProductLine = apps.get_model("app", "ProductLine")
    for code, name, color, icon, order in PRODUCTS:
        ProductLine.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "color": color,
                "icon": icon,
                "sort_order": order,
                "active": True,
            },
        )


def unseed_products(apps, schema_editor):
    ProductLine = apps.get_model("app", "ProductLine")
    ProductLine.objects.filter(code__in=[row[0] for row in PRODUCTS]).delete()


class Migration(migrations.Migration):
    dependencies = [("app", "0053_help_article_editor")]

    operations = [
        migrations.CreateModel(
            name="ProductLine",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("code", models.SlugField(max_length=64, unique=True)),
                ("name", models.CharField(max_length=100, unique=True)),
                ("color", models.CharField(default="#64748b", max_length=7)),
                ("icon", models.CharField(blank=True, default="fa-cube", max_length=64)),
                ("sort_order", models.PositiveSmallIntegerField(default=0)),
                ("active", models.BooleanField(db_index=True, default=True)),
            ],
            options={"ordering": ["sort_order", "name"]},
        ),
        migrations.AddField(
            model_name="ticket",
            name="product_line",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="tickets",
                to="app.productline",
            ),
        ),
        migrations.AddIndex(
            model_name="ticket",
            index=models.Index(
                fields=["is_deleted", "merged_into", "product_line", "created_at"],
                name="app_ticket_product_created",
            ),
        ),
        migrations.AddIndex(
            model_name="ticket",
            index=models.Index(
                fields=["is_deleted", "merged_into", "product_line", "status"],
                name="app_ticket_product_status",
            ),
        ),
        migrations.RunPython(seed_products, unseed_products),
    ]
