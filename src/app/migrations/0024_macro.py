from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0023_ticket_event'),
    ]

    operations = [
        migrations.CreateModel(
            name='Macro',
            fields=[
                ('id', models.AutoField(auto_created=True, primary_key=True, serialize=False)),
                ('name', models.CharField(max_length=255)),
                ('description', models.CharField(blank=True, max_length=500, null=True)),
                ('actions', models.JSONField(default=dict)),
                ('active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
            ],
        ),
    ]
