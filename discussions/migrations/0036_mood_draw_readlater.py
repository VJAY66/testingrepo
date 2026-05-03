from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('discussions', '0035_quote_post_debate_timer'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # Post mood tag
        migrations.AddField(
            model_name='post',
            name='mood',
            field=models.CharField(
                blank=True, default='', db_index=True, max_length=20,
                choices=[
                    ('controversial', '🔥 Controversial'),
                    ('educational', '💡 Educational'),
                    ('funny', '😂 Funny'),
                    ('mindblowing', '🤯 Mind-blowing'),
                    ('emotional', '💔 Emotional'),
                    ('news', '📰 News'),
                    ('unpopular', '🙃 Unpopular Opinion'),
                    ('hottake', '☄️ Hot Take'),
                ],
            ),
        ),
        # Debate mutual draw
        migrations.AddField(
            model_name='debate',
            name='outcome',
            field=models.CharField(blank=True, default='', max_length=10,
                                   help_text="'draw' if both agreed to mutual draw"),
        ),
        migrations.AddField(
            model_name='debate',
            name='draw_proposed_by',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='draw_proposals',
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        # Read Later queue
        migrations.CreateModel(
            name='ReadLater',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('is_read', models.BooleanField(default=False)),
                ('added_at', models.DateTimeField(auto_now_add=True)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='read_later_by', to='discussions.post')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                                           related_name='read_later', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-added_at']},
        ),
        migrations.AddConstraint(
            model_name='readlater',
            constraint=models.UniqueConstraint(fields=['user', 'post'], name='unique_read_later'),
        ),
    ]
