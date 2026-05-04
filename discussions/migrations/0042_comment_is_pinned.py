from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0041_postreport'),
    ]

    operations = [
        migrations.AddField(
            model_name='comment',
            name='is_pinned',
            field=models.BooleanField(default=False, db_index=True, help_text='Post author pinned this comment'),
        ),
    ]
