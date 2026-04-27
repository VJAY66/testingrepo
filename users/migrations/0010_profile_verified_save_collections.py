from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('users', '0009_profile_bio_website_userblock'),
        ('discussions', '0026_poll_expires_at'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='profile',
            name='is_verified',
            field=models.BooleanField(default=False, help_text='Manually verified by a moderator'),
        ),
        migrations.CreateModel(
            name='SaveCollection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=60)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='save_collections', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(
            model_name='savecollection',
            constraint=models.UniqueConstraint(fields=['user', 'name'], name='unique_collection_per_user'),
        ),
        migrations.CreateModel(
            name='CollectionItem',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('added_at', models.DateTimeField(auto_now_add=True)),
                ('collection', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='items', to='users.savecollection')),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='collection_items', to='discussions.post')),
            ],
            options={'ordering': ['-added_at']},
        ),
        migrations.AddConstraint(
            model_name='collectionitem',
            constraint=models.UniqueConstraint(fields=['collection', 'post'], name='unique_collection_item'),
        ),
    ]
