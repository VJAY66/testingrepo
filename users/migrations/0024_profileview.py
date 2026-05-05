from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0023_profile_streak_grace'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='hide_profile_views',
            field=models.BooleanField(default=False, help_text="When True, this user's profile visits are not recorded and they cannot see who viewed them"),
        ),
        migrations.CreateModel(
            name='ProfileView',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('viewed_at', models.DateTimeField(auto_now=True)),
                ('viewed', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='profile_views_received', to=settings.AUTH_USER_MODEL)),
                ('viewer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='profiles_viewed', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-viewed_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='profileview',
            constraint=models.UniqueConstraint(fields=['viewer', 'viewed'], name='unique_profile_view'),
        ),
        migrations.AddIndex(
            model_name='profileview',
            index=models.Index(fields=['viewed', 'viewed_at'], name='users_profi_viewed_i_idx'),
        ),
    ]
