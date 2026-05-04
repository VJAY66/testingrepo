from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0020_userban'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='totp_secret',
            field=models.CharField(blank=True, default='', help_text='TOTP secret for 2FA (empty = disabled)', max_length=64),
        ),
        migrations.AddField(
            model_name='profile',
            name='totp_enabled',
            field=models.BooleanField(default=False, db_index=True, help_text='True when 2FA is fully set up and active'),
        ),
    ]
