from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0045_pollprediction'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PostReminder',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('remind_at', models.DateTimeField(db_index=True)),
                ('preset', models.CharField(
                    choices=[('1h', 'In 1 hour'), ('tomorrow', 'Tomorrow'), ('next_week', 'Next week'), ('custom', 'Custom time')],
                    default='custom', max_length=10,
                )),
                ('is_sent', models.BooleanField(default=False, db_index=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reminders', to='discussions.post')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='post_reminders', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['remind_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='postreminder',
            constraint=models.UniqueConstraint(fields=['user', 'post'], name='unique_post_reminder'),
        ),
        migrations.AddIndex(
            model_name='postreminder',
            index=models.Index(fields=['is_sent', 'remind_at'], name='discussions_is_sent_remind_idx'),
        ),
    ]
