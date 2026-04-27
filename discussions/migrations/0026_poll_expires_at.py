from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0025_hashtag_follow_comment_reply'),
    ]

    operations = [
        migrations.AddField(
            model_name='poll',
            name='expires_at',
            field=models.DateTimeField(blank=True, help_text='Auto-close this poll at this time. Leave blank for no expiry.', null=True),
        ),
    ]
