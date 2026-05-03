from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('discussions', '0034_instagram_features'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='quoted_post',
            field=models.ForeignKey(
                blank=True, null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name='quotes',
                to='discussions.post',
            ),
        ),
        migrations.AddField(
            model_name='debate',
            name='round_duration_minutes',
            field=models.PositiveSmallIntegerField(default=10, help_text='Minutes per debate round'),
        ),
        migrations.AddField(
            model_name='debate',
            name='round_ends_at',
            field=models.DateTimeField(blank=True, null=True, help_text='When the current round timer expires'),
        ),
    ]
