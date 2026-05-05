from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0021_profile_totp_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='quiet_hours_start',
            field=models.TimeField(blank=True, help_text='No notification emails sent after this time', null=True),
        ),
        migrations.AddField(
            model_name='profile',
            name='quiet_hours_end',
            field=models.TimeField(blank=True, help_text='Notification emails resume at this time', null=True),
        ),
    ]
