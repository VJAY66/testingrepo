# Generated migration for notification count field and new author notification types

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0018_debatemessagereaction_profilereport'),
    ]

    operations = [
        migrations.AddField(
            model_name='notification',
            name='count',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AlterField(
            model_name='notification',
            name='notification_type',
            field=models.CharField(
                choices=[
                    ('post_comment', 'New Comment on Followed Post'),
                    ('post_activity', 'Active Conversation on Followed Post'),
                    ('moderation_alert', 'Moderator Alert'),
                    ('moderation_warning', 'Moderation Warning'),
                    ('author_comment', 'New Comment on Your Post'),
                    ('author_debate', 'New Debate on Your Post'),
                    ('author_repost', 'Your Post was Reposted'),
                    ('author_save', 'Your Post was Saved'),
                ],
                max_length=20,
            ),
        ),
    ]
