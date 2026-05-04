from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("discussions", "0039_dmrequest"),
    ]

    operations = [
        migrations.AddField(
            model_name="post",
            name="reply_restriction",
            field=models.CharField(
                choices=[
                    ("everyone", "Everyone"),
                    ("followers", "Followers"),
                    ("close_friends", "Close Friends"),
                    ("nobody", "Nobody"),
                ],
                default="everyone",
                max_length=20,
            ),
        ),
    ]
