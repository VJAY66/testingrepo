from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0010_profile_verified_save_collections'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='MutedKeyword',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('keyword', models.CharField(db_index=True, max_length=60)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='muted_keywords', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'ordering': ['keyword'],
            },
        ),
        migrations.AddConstraint(
            model_name='mutedkeyword',
            constraint=models.UniqueConstraint(fields=['user', 'keyword'], name='unique_muted_keyword'),
        ),
    ]
