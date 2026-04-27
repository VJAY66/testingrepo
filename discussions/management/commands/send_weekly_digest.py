from django.core.management.base import BaseCommand
from django.conf import settings
from django.db.models import Q, Count
from django.utils import timezone
from datetime import timedelta

from django.contrib.auth.models import User
from discussions.models import Post


class Command(BaseCommand):
    help = 'Send weekly digest emails to users with interest categories set.'

    def handle(self, *args, **options):
        from django.core.mail import send_mail
        cutoff = timezone.now() - timedelta(days=7)
        sent = 0
        users = User.objects.filter(email__gt='').select_related('profile')
        for user in users:
            profile = getattr(user, 'profile', None)
            cats = list(profile.interested_categories or []) if profile else []
            if not cats:
                continue
            posts = Post.objects.filter(
                category__in=cats, created_at__gte=cutoff, is_draft=False, is_deleted_by_moderation=False
            ).annotate(
                score=Count('actions', filter=Q(actions__action='like')) + Count('comments')
            ).order_by('-score')[:5]
            if not posts:
                continue
            lines = [f'Top posts this week in your categories ({", ".join(cats[:3])}):\n']
            for i, p in enumerate(posts, 1):
                lines.append(f'{i}. {p.title}\n   {settings.SITE_URL}/discussion/{p.id}/\n')
            body = '\n'.join(lines) + f'\n\nVisit {settings.SITE_URL} to see more.\nUnsubscribe by updating your interests.'
            try:
                send_mail(
                    f'Your weekly PickASide digest',
                    body,
                    settings.DEFAULT_FROM_EMAIL,
                    [user.email],
                    fail_silently=True,
                )
                sent += 1
            except Exception:
                pass
        self.stdout.write(self.style.SUCCESS(f'Sent digest to {sent} user(s).'))
