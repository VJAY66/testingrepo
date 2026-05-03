from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0033_debateparticipant_last_read'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Post: add audience, reading_time_minutes, word_count
        migrations.AddField(
            model_name='post',
            name='audience',
            field=models.CharField(
                choices=[('public', 'Public'), ('followers', 'Followers Only'), ('close_friends', 'Close Friends')],
                default='public',
                db_index=True,
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='post',
            name='reading_time_minutes',
            field=models.PositiveSmallIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='post',
            name='word_count',
            field=models.PositiveIntegerField(default=0),
        ),
        # Notification: add actors
        migrations.AddField(
            model_name='notification',
            name='actors',
            field=models.JSONField(blank=True, default=list, help_text='Usernames of users involved, for batched display'),
        ),
        # Story
        migrations.CreateModel(
            name='Story',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('content', models.TextField(blank=True, default='')),
                ('image', models.ImageField(blank=True, null=True, upload_to='stories/')),
                ('bg_color', models.CharField(default='#0ea5e9', max_length=20)),
                ('expires_at', models.DateTimeField(db_index=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='stories', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        # StoryView
        migrations.CreateModel(
            name='StoryView',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('viewed_at', models.DateTimeField(auto_now_add=True)),
                ('story', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='views', to='discussions.story')),
                ('viewer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='story_views', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-viewed_at']},
        ),
        migrations.AddConstraint(
            model_name='storyview',
            constraint=models.UniqueConstraint(fields=['story', 'viewer'], name='unique_story_view'),
        ),
        # FeedScore
        migrations.CreateModel(
            name='FeedScore',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('score', models.FloatField(db_index=True, default=0.0)),
                ('computed_at', models.DateTimeField(auto_now=True)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feed_scores', to='discussions.post')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='feed_scores', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-score']},
        ),
        migrations.AddConstraint(
            model_name='feedscore',
            constraint=models.UniqueConstraint(fields=['user', 'post'], name='unique_feed_score'),
        ),
        # PostInsight
        migrations.CreateModel(
            name='PostInsight',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('date', models.DateField(db_index=True)),
                ('unique_viewers', models.PositiveIntegerField(default=0)),
                ('total_impressions', models.PositiveIntegerField(default=0)),
                ('likes_count', models.PositiveIntegerField(default=0)),
                ('comments_count', models.PositiveIntegerField(default=0)),
                ('saves_count', models.PositiveIntegerField(default=0)),
                ('debates_count', models.PositiveIntegerField(default=0)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='insights', to='discussions.post')),
            ],
            options={'ordering': ['-date']},
        ),
        migrations.AddConstraint(
            model_name='postinsight',
            constraint=models.UniqueConstraint(fields=['post', 'date'], name='unique_post_insight_day'),
        ),
    ]
