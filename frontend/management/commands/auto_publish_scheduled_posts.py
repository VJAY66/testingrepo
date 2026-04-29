from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = 'Publish scheduled draft posts whose scheduled_for time has passed'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print what would be published without saving')

    def handle(self, *args, **options):
        from discussions.models import Post
        dry_run = options['dry_run']
        now = timezone.now()
        due = Post.objects.filter(is_draft=True, scheduled_for__lte=now, scheduled_for__isnull=False)
        count = due.count()
        if count == 0:
            self.stdout.write('No scheduled posts due for publishing.')
            return
        for post in due:
            self.stdout.write(f'{"[DRY RUN] " if dry_run else ""}Publishing: "{post.title}" by {post.user.username} (scheduled {post.scheduled_for})')
            if not dry_run:
                post.is_draft = False
                post.scheduled_for = None
                post.save(update_fields=['is_draft', 'scheduled_for', 'updated_at'])
        if not dry_run:
            self.stdout.write(self.style.SUCCESS(f'Published {count} post(s).'))
        else:
            self.stdout.write(f'[DRY RUN] Would publish {count} post(s).')
