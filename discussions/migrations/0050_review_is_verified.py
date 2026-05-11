from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0049_comment_is_anonymous'),
    ]

    operations = [
        migrations.AddField(
            model_name='review',
            name='is_verified',
            field=models.BooleanField(default=False, help_text='Author confirms they actually used/watched/visited the subject'),
        ),
    ]
