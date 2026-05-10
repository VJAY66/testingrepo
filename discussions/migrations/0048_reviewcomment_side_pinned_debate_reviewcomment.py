from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0047_post_is_anonymous'),
    ]

    operations = [
        migrations.AddField(
            model_name='reviewcomment',
            name='side',
            field=models.CharField(
                choices=[('agree', 'Agree'), ('disagree', 'Disagree')],
                default='agree',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='reviewcomment',
            name='is_pinned',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterModelOptions(
            name='reviewcomment',
            options={'ordering': ['-is_pinned', 'created_at']},
        ),
        migrations.AddField(
            model_name='debate',
            name='review_comment',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='debates',
                to='discussions.reviewcomment',
            ),
        ),
    ]
