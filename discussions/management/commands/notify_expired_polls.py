from django.core.management.base import BaseCommand
from django.utils import timezone
from discussions.models import Poll, PollFollow, Notification


class Command(BaseCommand):
    help = 'Notify poll followers when a poll has expired. Run every few minutes via cron.'

    def handle(self, *args, **options):
        now = timezone.now()
        expired_polls = Poll.objects.filter(
            expires_at__lte=now,
            expiry_notified=False,
            is_deleted_by_moderation=False,
        ).prefetch_related('followers__user')

        notified = 0
        for poll in expired_polls:
            followers = poll.followers.select_related('user').exclude(user=poll.user)
            for follow in followers:
                Notification.objects.get_or_create(
                    user=follow.user,
                    notification_type='poll_closed',
                    message=f'Poll "{poll.title}" has now closed. See the final results.',
                    defaults={'post': None},
                )
                # Send email via helper imported lazily to avoid circular imports
                from django.conf import settings
                from django.core.mail import send_mail
                if follow.user.email:
                    try:
                        send_mail(
                            f'Poll closed: "{poll.title}"',
                            f'The poll you followed has closed.\n\n'
                            f'"{poll.title}"\n\n'
                            f'View the final results at: {settings.SITE_URL}/polls/{poll.id}/',
                            settings.DEFAULT_FROM_EMAIL,
                            [follow.user.email],
                            fail_silently=True,
                        )
                    except Exception:
                        pass
            poll.expiry_notified = True
            poll.save(update_fields=['expiry_notified', 'updated_at'])
            notified += 1

        self.stdout.write(self.style.SUCCESS(f'Notified followers of {notified} expired poll(s).'))
