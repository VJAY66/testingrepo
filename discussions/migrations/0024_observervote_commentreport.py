from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0023_content_actions'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ObserverVote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('winner_side', models.CharField(choices=[('yes', 'Yes'), ('no', 'No')], max_length=10)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('debate', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='observer_votes', to='discussions.debate')),
                ('voter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='observer_votes_cast', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='observervote',
            constraint=models.UniqueConstraint(fields=['debate', 'voter'], name='unique_observer_vote_per_debate'),
        ),
        migrations.CreateModel(
            name='CommentReport',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.CharField(
                    choices=[
                        ('abusive_language', 'Abusive language'),
                        ('spam', 'Spam or misleading'),
                        ('misinformation', 'Misinformation'),
                        ('harassment', 'Harassment'),
                        ('other', 'Other'),
                    ],
                    default='abusive_language',
                    max_length=30,
                )),
                ('details', models.TextField(blank=True, default='')),
                ('status', models.CharField(
                    choices=[('pending', 'Pending'), ('dismissed', 'Dismissed'), ('actioned', 'Actioned')],
                    default='pending',
                    max_length=20,
                )),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('comment', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reports', to='discussions.comment')),
                ('reporter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='comment_reports_submitted', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='commentreport',
            constraint=models.UniqueConstraint(fields=['comment', 'reporter'], name='unique_comment_report_per_reporter'),
        ),
    ]
