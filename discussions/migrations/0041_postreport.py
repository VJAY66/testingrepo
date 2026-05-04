import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("discussions", "0040_post_reply_restriction"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name="PostReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("reason", models.CharField(
                    choices=[
                        ("spam", "Spam or misleading"),
                        ("misinformation", "Misinformation"),
                        ("harassment", "Harassment"),
                        ("hate_speech", "Hate speech"),
                        ("other", "Other"),
                    ],
                    default="spam",
                    max_length=30,
                )),
                ("details", models.TextField(blank=True, default="")),
                ("status", models.CharField(
                    choices=[
                        ("pending", "Pending"),
                        ("dismissed", "Dismissed"),
                        ("actioned", "Actioned"),
                    ],
                    default="pending",
                    max_length=20,
                )),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("post", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="reports",
                    to="discussions.post",
                )),
                ("reporter", models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name="post_reports_submitted",
                    to=settings.AUTH_USER_MODEL,
                )),
            ],
            options={
                "ordering": ["-created_at"],
                "constraints": [
                    models.UniqueConstraint(
                        fields=("post", "reporter"),
                        name="unique_post_report_per_reporter",
                    )
                ],
            },
        ),
    ]
