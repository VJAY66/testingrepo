"""Management command: send streak-at-risk notifications to users who haven't been active today."""
import datetime
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.core.mail import send_mail
from django.conf import settings
from users.models import Profile
from discussions.models import Notification


class Command(BaseCommand):
    help = 'Notify users whose activity streak will reset if they do not act today.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print counts without sending.')

    def handle(self, *args, **options):
        dry_run = options['dry_run']
        today = timezone.now().date()
        yesterday = today - datetime.timedelta(days=1)

        # Users whose last activity was exactly yesterday (streak alive, not yet active today)
        at_risk = Profile.objects.filter(
            last_activity_date=yesterday,
            streak_days__gte=2,
        ).select_related('user')

        sent = skipped = 0
        for profile in at_risk:
            user = profile.user

            if dry_run:
                self.stdout.write(f'[dry-run] Would notify {user.username} (streak={profile.streak_days})')
                sent += 1
                continue

            # In-app notification (deduped — only one per day)
            Notification.objects.get_or_create(
                user=user,
                notification_type='post_reminder',
                message=f'🔥 Your {profile.streak_days}-day streak is at risk! Post or comment today to keep it alive.',
                defaults={'is_read': False},
            )

            # Email (if opted in and have address)
            if getattr(user, 'email', None):
                try:
                    prefs = profile.notification_prefs or {}
                except Exception:
                    prefs = {}
                if prefs.get('weekly_digest', {}).get('email', True):
                    try:
                        send_mail(
                            f'🔥 Your {profile.streak_days}-day streak is at risk',
                            f'Hey {user.username},\n\n'
                            f'Your {profile.streak_days}-day activity streak will reset at midnight '
                            f'unless you post or comment today.\n\n'
                            f'Keep it going: {getattr(settings, "SITE_URL", "")}/\n',
                            settings.DEFAULT_FROM_EMAIL,
                            [user.email],
                            fail_silently=True,
                        )
                    except Exception:
                        pass

            sent += 1

        self.stdout.write(
            self.style.SUCCESS(
                f'{"[dry-run] " if dry_run else ""}Notified {sent} users, skipped {skipped}.'
            )
        )
