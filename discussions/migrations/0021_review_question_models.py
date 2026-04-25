# Generated manually for Phase 2: Review and Question models

import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


CATEGORY_CHOICES = [
    ("Astrology", "Astrology"),
    ("Beauty", "Beauty"),
    ("Business", "Business"),
    ("Education", "Education"),
    ("Entertainment", "Entertainment"),
    ("Fashion", "Fashion"),
    ("Food", "Food"),
    ("Gadgets", "Gadgets"),
    ("Health", "Health"),
    ("History", "History"),
    ("Investment", "Investment"),
    ("Medicenes", "Medicenes"),
    ("Music", "Music"),
    ("Painting", "Painting"),
    ("Photography", "Photography"),
    ("Politics", "Politics"),
    ("Relationships", "Relationships"),
    ("Science", "Science"),
    ("Spirituality", "Spirituality"),
    ("Sports", "Sports"),
    ("Technology", "Technology"),
    ("Travel", "Travel"),
    ("Vehicles", "Vehicles"),
    ("Others", "Others"),
]


class Migration(migrations.Migration):

    dependencies = [
        ("discussions", "0020_poll_models"),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # ── Question ──────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Question",
            fields=[
                ("id", models.CharField(max_length=36, primary_key=True, serialize=False)),
                ("title", models.CharField(max_length=255)),
                ("content", models.TextField(blank=True, default="")),
                ("category", models.CharField(choices=CATEGORY_CHOICES, max_length=50)),
                ("hashtags", models.TextField(blank=True, default="")),
                ("answer_count", models.IntegerField(default=0)),
                ("is_deleted_by_moderation", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="questions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        # ── Answer ────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Answer",
            fields=[
                ("id", models.CharField(max_length=36, primary_key=True, serialize=False)),
                ("content", models.TextField()),
                ("upvotes", models.IntegerField(default=0)),
                ("downvotes", models.IntegerField(default=0)),
                ("is_deleted_by_moderation", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "question",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="answers",
                        to="discussions.question",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="answers",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-upvotes", "created_at"]},
        ),
        # Add best_answer FK to Question (after Answer exists)
        migrations.AddField(
            model_name="question",
            name="best_answer",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="best_for_question",
                to="discussions.answer",
            ),
        ),
        # ── AnswerVote ────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="AnswerVote",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("vote", models.CharField(choices=[("up", "Upvote"), ("down", "Downvote")], max_length=5)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "answer",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="votes",
                        to="discussions.answer",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="answer_votes",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
                "constraints": [
                    models.UniqueConstraint(fields=("answer", "user"), name="unique_answer_vote_per_user")
                ],
            },
        ),
        # ── Review ────────────────────────────────────────────────────────────
        migrations.CreateModel(
            name="Review",
            fields=[
                ("id", models.CharField(max_length=36, primary_key=True, serialize=False)),
                ("subject", models.CharField(max_length=255)),
                (
                    "subject_type",
                    models.CharField(
                        choices=[
                            ("Movie", "Movie"),
                            ("TV Show", "TV Show"),
                            ("Book", "Book"),
                            ("Music / Album", "Music / Album"),
                            ("Product", "Product"),
                            ("Place", "Place"),
                            ("Restaurant", "Restaurant"),
                            ("App / Game", "App / Game"),
                            ("Person", "Person"),
                            ("Other", "Other"),
                        ],
                        max_length=50,
                    ),
                ),
                ("rating", models.PositiveSmallIntegerField()),
                ("content", models.TextField()),
                ("category", models.CharField(choices=CATEGORY_CHOICES, max_length=50)),
                ("hashtags", models.TextField(blank=True, default="")),
                ("agree_count", models.IntegerField(default=0)),
                ("disagree_count", models.IntegerField(default=0)),
                ("is_deleted_by_moderation", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reviews",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["-created_at"]},
        ),
        # ── ReviewReaction ────────────────────────────────────────────────────
        migrations.CreateModel(
            name="ReviewReaction",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("reaction", models.CharField(choices=[("agree", "Agree"), ("disagree", "Disagree")], max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "review",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reactions",
                        to="discussions.review",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="review_reactions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
                "constraints": [
                    models.UniqueConstraint(fields=("review", "user"), name="unique_review_reaction_per_user")
                ],
            },
        ),
        # ── ReviewComment ─────────────────────────────────────────────────────
        migrations.CreateModel(
            name="ReviewComment",
            fields=[
                ("id", models.CharField(max_length=36, primary_key=True, serialize=False)),
                ("content", models.TextField()),
                ("likes", models.IntegerField(default=0)),
                ("dislikes", models.IntegerField(default=0)),
                ("is_deleted_by_moderation", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "review",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="comments",
                        to="discussions.review",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="review_comments",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={"ordering": ["created_at"]},
        ),
        # ── ReviewCommentReaction ─────────────────────────────────────────────
        migrations.CreateModel(
            name="ReviewCommentReaction",
            fields=[
                (
                    "id",
                    models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID"),
                ),
                ("reaction", models.CharField(choices=[("like", "Like"), ("dislike", "Dislike")], max_length=10)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                (
                    "comment",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="reactions",
                        to="discussions.reviewcomment",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="review_comment_reactions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ["-updated_at"],
                "constraints": [
                    models.UniqueConstraint(fields=("comment", "user"), name="unique_review_comment_reaction")
                ],
            },
        ),
    ]
