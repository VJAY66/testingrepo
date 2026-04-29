import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('users', '0014_collectionitem_tag'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]
    operations = [
        migrations.AddField(model_name='profile', name='reputation_score',
            field=models.IntegerField(db_index=True, default=0, help_text='Computed reputation from likes, answers, debates')),
        migrations.AddField(model_name='profile', name='streak_days',
            field=models.PositiveIntegerField(default=0, help_text='Current consecutive days of activity')),
        migrations.AddField(model_name='profile', name='last_activity_date',
            field=models.DateField(blank=True, help_text='Last date the user posted or commented', null=True)),
        migrations.CreateModel(name='Endorsement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('topic', models.CharField(max_length=60)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('endorser', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='given_endorsements', to=settings.AUTH_USER_MODEL)),
                ('endorsed', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='received_endorsements', to=settings.AUTH_USER_MODEL)),
            ],
            options={'ordering': ['-created_at']},
        ),
        migrations.AddConstraint(model_name='endorsement',
            constraint=models.UniqueConstraint(fields=['endorser', 'endorsed', 'topic'], name='unique_endorsement')),
    ]
