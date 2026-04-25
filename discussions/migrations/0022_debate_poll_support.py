import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0021_review_question_models'),
    ]

    operations = [
        # Make comment nullable on Debate
        migrations.AlterField(
            model_name='debate',
            name='comment',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='debates',
                to='discussions.comment',
            ),
        ),
        # Make post nullable on Debate
        migrations.AlterField(
            model_name='debate',
            name='post',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='debates',
                to='discussions.post',
            ),
        ),
        # Add poll FK
        migrations.AddField(
            model_name='debate',
            name='poll',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='debates',
                to='discussions.poll',
            ),
        ),
        # Add poll_comment FK
        migrations.AddField(
            model_name='debate',
            name='poll_comment',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='debates',
                to='discussions.pollcomment',
            ),
        ),
        # Make Notification.post nullable so poll-debate notifications can omit the post
        migrations.AlterField(
            model_name='notification',
            name='post',
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.CASCADE,
                related_name='notifications',
                to='discussions.post',
            ),
        ),
    ]
