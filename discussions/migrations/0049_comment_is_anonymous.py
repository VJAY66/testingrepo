from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0048_reviewcomment_side_pinned_debate_reviewcomment'),
    ]

    operations = [
        migrations.AddField(
            model_name='comment',
            name='is_anonymous',
            field=models.BooleanField(default=False),
        ),
    ]
