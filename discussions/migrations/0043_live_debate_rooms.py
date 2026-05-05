import uuid
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0042_comment_is_pinned'),
        ('auth', '0012_alter_user_first_name_max_length'),
    ]

    operations = [
        migrations.CreateModel(
            name='LiveDebateRoom',
            fields=[
                ('id', models.CharField(max_length=36, primary_key=True, serialize=False)),
                ('title', models.CharField(max_length=200)),
                ('description', models.TextField(blank=True, default='')),
                ('status', models.CharField(
                    choices=[('open', 'Open — waiting for debaters'), ('live', 'Live — debate in progress'),
                             ('voting', 'Voting — community voting on winner'), ('closed', 'Closed')],
                    db_index=True, default='open', max_length=10)),
                ('winner_side', models.CharField(
                    blank=True, choices=[('yes', 'Yes'), ('no', 'No')], default='', max_length=3)),
                ('duration_minutes', models.PositiveSmallIntegerField(default=10)),
                ('started_at', models.DateTimeField(blank=True, null=True)),
                ('ends_at', models.DateTimeField(blank=True, null=True)),
                ('ended_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('creator', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='created_debate_rooms', to='auth.user')),
                ('yes_debater', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='debate_rooms_yes', to='auth.user')),
                ('no_debater', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                    related_name='debate_rooms_no', to='auth.user')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.CreateModel(
            name='LiveDebateMessage',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('content', models.TextField(max_length=1000)),
                ('is_system', models.BooleanField(default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='messages', to='discussions.livedebateroom')),
                ('sender', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='live_debate_messages', to='auth.user')),
            ],
            options={'ordering': ['created_at']},
        ),
        migrations.CreateModel(
            name='LiveDebateVote',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('winner_side', models.CharField(choices=[('yes', 'Yes'), ('no', 'No')], max_length=3)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('room', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='live_votes', to='discussions.livedebateroom')),
                ('voter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE,
                    related_name='live_debate_votes', to='auth.user')),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(
            model_name='livedebatevote',
            constraint=models.UniqueConstraint(fields=['room', 'voter'], name='unique_live_debate_vote'),
        ),
    ]
