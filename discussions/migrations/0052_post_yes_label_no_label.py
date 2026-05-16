from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0051_notification_type_max_length'),
    ]

    operations = [
        migrations.AddField(
            model_name='post',
            name='yes_label',
            field=models.CharField(default='Yes', help_text='Custom label for the left/yes side option', max_length=50),
        ),
        migrations.AddField(
            model_name='post',
            name='no_label',
            field=models.CharField(default='No', help_text='Custom label for the right/no side option', max_length=50),
        ),
    ]
