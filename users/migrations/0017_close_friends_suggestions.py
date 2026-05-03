from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0016_profile_theme'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CloseFriend',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='close_friends_list', to=settings.AUTH_USER_MODEL)),
                ('friend', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='in_close_friends_of', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(
            model_name='closefriend',
            constraint=models.UniqueConstraint(fields=['user', 'friend'], name='unique_close_friend'),
        ),
        migrations.CreateModel(
            name='UserSuggestion',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False)),
                ('reason', models.CharField(
                    choices=[('mutual_follow', 'Mutual Follows'), ('shared_interest', 'Shared Interests'), ('shared_hashtag', 'Shared Hashtags')],
                    default='mutual_follow',
                    max_length=20,
                )),
                ('reason_detail', models.CharField(blank=True, default='', max_length=120)),
                ('score', models.FloatField(default=0.0)),
                ('computed_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='suggestions_for', to=settings.AUTH_USER_MODEL)),
                ('suggested_user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='suggested_to', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-score']},
        ),
        migrations.AddConstraint(
            model_name='usersuggestion',
            constraint=models.UniqueConstraint(fields=['user', 'suggested_user'], name='unique_user_suggestion'),
        ),
    ]
