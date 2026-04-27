from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0027_post_draft_pinned_debateview_poll_expiry_notified'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='poll',
            name='is_anonymous',
            field=models.BooleanField(default=False, help_text='Hide voter identities from results'),
        ),
        migrations.CreateModel(
            name='PostSeries',
            fields=[
                ('id', models.CharField(max_length=36, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=120)),
                ('description', models.TextField(blank=True, default='')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='post_series', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name_plural': 'Post series',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PostSeriesItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order', models.PositiveIntegerField(default=0)),
                ('series', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='discussions.postseries')),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='series_items', to='discussions.post')),
            ],
            options={
                'ordering': ['order'],
            },
        ),
        migrations.AddConstraint(
            model_name='postseriesitem',
            constraint=models.UniqueConstraint(fields=['series', 'post'], name='unique_series_post'),
        ),
    ]
