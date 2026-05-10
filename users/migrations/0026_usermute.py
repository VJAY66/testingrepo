from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0025_profile_private_followrequest'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='UserMute',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('muted', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='muted_by', to=settings.AUTH_USER_MODEL)),
                ('muter', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='muting', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddConstraint(
            model_name='usermute',
            constraint=models.UniqueConstraint(fields=['muter', 'muted'], name='unique_user_mute'),
        ),
    ]
