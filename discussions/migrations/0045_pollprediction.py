import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0044_debate_counter_proposal'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='PollPrediction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('was_correct', models.BooleanField(blank=True, help_text='Set when the poll closes; null = unresolved', null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('poll', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='predictions', to='discussions.poll')),
                ('predicted_option', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='predictions', to='discussions.polloption')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='poll_predictions', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(
            model_name='pollprediction',
            constraint=models.UniqueConstraint(fields=['user', 'poll'], name='unique_poll_prediction'),
        ),
    ]
