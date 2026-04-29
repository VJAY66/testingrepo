from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0031_post_closes_at_is_hot_debate_rematch'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='poll',
            name='allows_ranked_choice',
            field=models.BooleanField(default=False, help_text='Allow voters to rank options in order of preference'),
        ),
        migrations.CreateModel(
            name='RankedChoiceVote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('rank', models.PositiveSmallIntegerField(help_text='1 = first choice, 2 = second, etc.')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('option', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ranked_votes', to='discussions.polloption')),
                ('poll', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ranked_votes', to='discussions.poll')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='ranked_votes', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['rank'],
            },
        ),
        migrations.AddConstraint(
            model_name='rankedchoicevote',
            constraint=models.UniqueConstraint(fields=('user', 'poll', 'option'), name='unique_ranked_choice_vote_option'),
        ),
        migrations.AddConstraint(
            model_name='rankedchoicevote',
            constraint=models.UniqueConstraint(fields=('user', 'poll', 'rank'), name='unique_ranked_choice_vote_rank'),
        ),
    ]
