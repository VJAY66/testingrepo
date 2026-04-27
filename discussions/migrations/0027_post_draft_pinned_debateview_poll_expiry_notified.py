from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0026_poll_expires_at'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='is_draft',
            field=models.BooleanField(default=False, help_text='Saved draft, not yet published'),
        ),
        migrations.AddField(
            model_name='post',
            name='is_pinned',
            field=models.BooleanField(default=False, help_text='Pinned to top of author profile'),
        ),
        migrations.CreateModel(
            name='DebateView',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('last_seen', models.DateTimeField(auto_now=True)),
                ('debate', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='spectators', to='discussions.debate')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='debate_views', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AddConstraint(
            model_name='debateview',
            constraint=models.UniqueConstraint(fields=['debate', 'user'], name='unique_debate_view'),
        ),
        migrations.AddField(
            model_name='poll',
            name='expiry_notified',
            field=models.BooleanField(default=False, help_text='Whether followers have been notified of poll closure'),
        ),
    ]
