from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from discussions.models import Post, Poll, Answer
from users.models import Achievement, ACHIEVEMENT_DEFS


class Command(BaseCommand):
    help = 'Award achievement badges to users who have met the criteria'

    def handle(self, *args, **options):
        awarded_total = 0
        for user in User.objects.select_related('profile').all():
            awarded_total += self._check_user(user)
        self.stdout.write(f'Awarded {awarded_total} new achievement(s).')

    def _check_user(self, user):
        awarded = 0
        try:
            profile = user.profile
        except Exception:
            profile = None

        posts_count = Post.objects.filter(user=user, is_draft=False).count()
        checks = []

        # first_post
        if posts_count >= 1:
            checks.append('first_post')

        # debate_starter: initiated at least 5 debates
        debates_initiated = user.debate_participations.filter(
            debate__initiator=user
        ).values('debate_id').distinct().count()
        if debates_initiated >= 5:
            checks.append('debate_starter')

        # top_voice: received 50 likes on posts
        from discussions.models import PostAction
        likes_total = PostAction.objects.filter(post__user=user, action='like').count()
        if likes_total >= 50:
            checks.append('top_voice')

        # helpful: has an answer marked as best
        if Answer.objects.filter(user=user, question__best_answer__user=user).exists():
            checks.append('helpful')

        # poll_master: created 10+ polls
        if Poll.objects.filter(user=user).count() >= 10:
            checks.append('poll_master')

        # verified_voice
        if profile and profile.is_verified:
            checks.append('verified_voice')

        # contributor: 10+ posts
        if posts_count >= 10:
            checks.append('contributor')

        # veteran: active for 30+ days
        if (timezone.now() - user.date_joined).days >= 30:
            checks.append('veteran')

        for code in checks:
            _, created = Achievement.objects.get_or_create(user=user, code=code)
            if created:
                awarded += 1

        return awarded
