from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0050_review_is_verified'),
    ]

    operations = [
        migrations.AlterField(
            model_name='notification',
            name='notification_type',
            field=models.CharField(max_length=30, choices=[
                ('post_comment', 'New Comment on Followed Post'),
                ('post_activity', 'Active Conversation on Followed Post'),
                ('moderation_alert', 'Moderator Alert'),
                ('moderation_warning', 'Moderation Warning'),
                ('author_comment', 'New Comment on Your Post'),
                ('author_debate', 'New Debate on Your Post'),
                ('author_repost', 'Your Post was Reposted'),
                ('author_save', 'Your Post was Saved'),
                ('author_like_milestone', 'Your Post Hit a Like Milestone'),
                ('mention', 'You Were Mentioned'),
                ('profile_view', 'Someone Viewed Your Profile'),
                ('post_reminder', 'Post Reminder'),
            ]),
        ),
    ]
