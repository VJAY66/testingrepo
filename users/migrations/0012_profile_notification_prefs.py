from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0011_muted_keyword'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='notification_prefs',
            field=models.JSONField(blank=True, default=dict, help_text='Per-type notification opt-in settings'),
        ),
    ]
