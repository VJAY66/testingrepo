from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0028_poll_anonymous_post_series'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='scheduled_for',
            field=models.DateTimeField(blank=True, help_text='Publish this draft automatically at this time', null=True),
        ),
    ]
