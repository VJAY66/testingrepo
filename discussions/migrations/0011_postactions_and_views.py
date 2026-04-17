from django.db import migrations, models
import django.db.models.deletion

class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0010_postfollow_notification_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='PostView',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('viewed_at', models.DateTimeField(auto_now_add=True)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='views', to='discussions.post')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='post_views', to='auth.user')),
            ],
            options={
                'ordering': ['-viewed_at'],
                'constraints': [
                    models.UniqueConstraint(fields=['user', 'post'], name='unique_post_view'),
                ],
            },
        ),
        migrations.CreateModel(
            name='PostAction',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('action', models.CharField(choices=[('like', 'Like'), ('save', 'Save'), ('repost', 'Repost')], max_length=20)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('post', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='actions', to='discussions.post')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='post_actions', to='auth.user')),
            ],
            options={
                'ordering': ['-updated_at'],
                'constraints': [
                    models.UniqueConstraint(fields=['user', 'post', 'action'], name='unique_post_action'),
                ],
            },
        ),
    ]
