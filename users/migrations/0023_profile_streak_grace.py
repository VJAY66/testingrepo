from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0022_profile_quiet_hours'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='streak_grace_used_at',
            field=models.DateField(blank=True, help_text='Date the weekly streak grace day was last used', null=True),
        ),
    ]
