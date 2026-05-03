from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0017_close_friends_suggestions'),
        ('discussions', '0035_quote_post_debate_timer'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProfileHighlight',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('order', models.PositiveSmallIntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('post', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='highlighted_by',
                    to='discussions.post',
                )),
                ('user', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='profile_highlights',
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={'ordering': ['order', '-created_at']},
        ),
        migrations.AddConstraint(
            model_name='profilehighlight',
            constraint=models.UniqueConstraint(fields=['user', 'post'], name='unique_profile_highlight'),
        ),
    ]
