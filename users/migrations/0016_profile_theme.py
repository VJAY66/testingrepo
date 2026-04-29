from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0015_profile_reputation_streak_endorsement'),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='theme',
            field=models.CharField(
                choices=[('light', 'Light'), ('dark', 'Dark')],
                default='light',
                help_text='User preferred color theme',
                max_length=10,
            ),
        ),
    ]
