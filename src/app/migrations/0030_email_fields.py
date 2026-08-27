from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('app', '0029_ticket_merged_into'),
    ]

    operations = [
        migrations.AddField(
            model_name='brand',
            name='support_email',
            field=models.EmailField(blank=True, default='', max_length=254),
        ),
        migrations.AddField(
            model_name='brand',
            name='from_name',
            field=models.CharField(blank=True, default='', max_length=100),
        ),
        migrations.AddField(
            model_name='brand',
            name='language',
            field=models.CharField(blank=True, default='es', max_length=10),
        ),
        migrations.AddField(
            model_name='brand',
            name='mailbox_type',
            field=models.CharField(
                choices=[('m365', 'Microsoft 365'), ('ses', 'Amazon SES')],
                default='m365',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='ticket',
            name='email_message_id',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
        migrations.AddField(
            model_name='comment',
            name='email_message_id',
            field=models.CharField(blank=True, db_index=True, max_length=255, null=True),
        ),
    ]
