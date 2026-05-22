from django.core.management.base import BaseCommand
from django.utils import timezone
from discussions.models import PostReminder, Notification


class Command(BaseCommand):
    help = 'Send in-app + web push notifications for due post reminders.'

    def add_arguments(self, parser):
        parser.add_argument('--dry-run', action='store_true', help='Print what would fire without saving')

    def handle(self, *args, **options):
        from frontend.views import _send_web_push
        dry_run = options['dry_run']
        now = timezone.now()
        due = PostReminder.objects.filter(is_sent=False, remind_at__lte=now).select_related('user', 'post')
        count = 0
        for reminder in due:
            title = f'⏰ Reminder: {reminder.post.title[:60]}'
            body = 'You asked to be reminded about this post. Tap to read it now.'
            try:
                post_url = f'/discussions/{reminder.post.id}/'
            except Exception:
                post_url = '/'
            if not dry_run:
                Notification.objects.get_or_create(
                    user=reminder.user,
                    notification_type='post_reminder',
                    message=title,
                    defaults={'post': reminder.post},
                )
                # Send web push notification
                _send_web_push(reminder.user, title, body, url=post_url)
                reminder.is_sent = True
                reminder.save(update_fields=['is_sent'])
            self.stdout.write(f'{"[DRY RUN] " if dry_run else ""}Fired reminder for {reminder.user.username}: {title}')
            count += 1
        mode = '[DRY RUN] ' if dry_run else ''
        self.stdout.write(self.style.SUCCESS(f'{mode}Sent {count} reminder(s).'))
