from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0046_postreminder'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='is_anonymous',
            field=models.BooleanField(default=False, help_text='Hide author identity from other users (moderators can still see)'),
        ),
    ]
