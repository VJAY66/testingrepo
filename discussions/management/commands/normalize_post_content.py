import re

from django.core.management.base import BaseCommand
from django.db import transaction

from discussions.models import Post


def normalize_post_content(content):
    text = (content or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    return re.sub(r"\n{3,}", "\n\n", text)


class Command(BaseCommand):
    help = "Normalize existing Post.content values by trimming and collapsing excessive blank lines."

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="Apply changes to database. Without this flag, runs as dry-run.",
        )

    def handle(self, *args, **options):
        apply_changes = options.get("apply", False)

        total_posts = Post.objects.count()
        changed_ids = []

        for post in Post.objects.only("id", "content"):
            original = post.content or ""
            normalized = normalize_post_content(original)
            if original != normalized:
                changed_ids.append((post.id, normalized))

        changed_count = len(changed_ids)

        if not apply_changes:
            self.stdout.write(self.style.WARNING("Dry-run mode: no changes written."))
            self.stdout.write(f"Total posts checked: {total_posts}")
            self.stdout.write(f"Posts needing normalization: {changed_count}")
            return

        if not changed_ids:
            self.stdout.write(self.style.SUCCESS("No posts required normalization."))
            self.stdout.write(f"Total posts checked: {total_posts}")
            return

        with transaction.atomic():
            for post_id, normalized in changed_ids:
                Post.objects.filter(id=post_id).update(content=normalized)

        self.stdout.write(self.style.SUCCESS("Normalization completed."))
        self.stdout.write(f"Total posts checked: {total_posts}")
        self.stdout.write(f"Posts updated: {changed_count}")
