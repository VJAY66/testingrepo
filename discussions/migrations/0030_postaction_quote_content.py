from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0029_post_scheduled_for'),
    ]

    operations = [
        migrations.AddField(
            model_name='postaction',
            name='quote_content',
            field=models.TextField(blank=True, default='', help_text='Optional comment when reposting'),
        ),
    ]
