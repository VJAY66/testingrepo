from django.core.management.base import BaseCommand
from django.utils import timezone
from discussions.models import Post


class Command(BaseCommand):
    help = 'Publish draft posts that have reached their scheduled_for time'

    def handle(self, *args, **options):
        now = timezone.now()
        posts = Post.objects.filter(
            is_draft=True,
            scheduled_for__isnull=False,
            scheduled_for__lte=now,
        )
        count = posts.count()
        posts.update(is_draft=False, scheduled_for=None)
        self.stdout.write(f'Published {count} scheduled post(s).')
