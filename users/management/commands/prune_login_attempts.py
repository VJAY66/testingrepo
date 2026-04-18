from datetime import timedelta

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone

from users.models import LoginAttempt


class Command(BaseCommand):
    help = 'Delete old login-attempt records based on retention policy.'

    def handle(self, *args, **options):
        retention_days = int(getattr(settings, 'LOGIN_ATTEMPT_RETENTION_DAYS', 30))
        cutoff = timezone.now() - timedelta(days=retention_days)
        deleted, _ = LoginAttempt.objects.filter(created_at__lt=cutoff).delete()
        self.stdout.write(
            self.style.SUCCESS(
                f'Pruned {deleted} login-attempt rows older than {retention_days} days.'
            )
        )
