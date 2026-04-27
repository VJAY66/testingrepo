"""
Management command: expire_pending_debates

Auto-rejects debate requests that have been pending for longer than 48 hours.
For each expired debate, posts a system message so participants can see what happened.

Usage:
    python manage.py expire_pending_debates
    python manage.py expire_pending_debates --hours 24
"""

from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta

from discussions.models import Debate, DebateMessage


class Command(BaseCommand):
    help = 'Auto-reject debate requests that have been pending for too long.'

    def add_arguments(self, parser):
        parser.add_argument(
            '--hours',
            type=int,
            default=48,
            help='Number of hours after which a pending debate is expired (default: 48)',
        )
        parser.add_argument(
            '--dry-run',
            action='store_true',
            help='Print what would be expired without actually changing anything.',
        )

    def handle(self, *args, **options):
        hours = options['hours']
        dry_run = options['dry_run']
        cutoff = timezone.now() - timedelta(hours=hours)

        pending = Debate.objects.filter(
            status='pending',
            created_at__lt=cutoff,
        ).select_related('initiator', 'target', 'post')

        count = pending.count()
        if count == 0:
            self.stdout.write(self.style.SUCCESS('No expired pending debates found.'))
            return

        if dry_run:
            self.stdout.write(f'[DRY RUN] Would expire {count} pending debate(s):')
            for debate in pending:
                self.stdout.write(
                    f'  #{debate.id} — {debate.initiator.username} → {debate.target.username} '
                    f'(created {debate.created_at:%Y-%m-%d %H:%M})'
                )
            return

        expired = 0
        for debate in pending:
            debate.status = 'rejected'
            debate.save(update_fields=['status'])

            DebateMessage.objects.create(
                debate=debate,
                sender=debate.initiator,
                content=(
                    f"This debate request expired automatically after {hours} hours "
                    f"without a response from @{debate.target.username}."
                ),
                is_system=True,
            )
            expired += 1

        self.stdout.write(
            self.style.SUCCESS(f'Expired {expired} pending debate(s) older than {hours} hours.')
        )
