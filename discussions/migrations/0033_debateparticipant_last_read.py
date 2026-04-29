from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('discussions', '0032_poll_ranked_choice'),
    ]

    operations = [
        migrations.AddField(
            model_name='debateparticipant',
            name='last_read_message_id',
            field=models.BigIntegerField(default=0, help_text='ID of the last message this participant has read'),
        ),
    ]
