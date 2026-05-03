"""
Personalised feed scoring — Instagram-style.

Score formula (per user × post pair):
  base      = likes×1 + comments×2 + saves×3 + debates×5 + hot_bonus×10
  interest  = 15  if post.category in user.interested_categories else 0
  following = 20  if post.user is followed by this user else 0
  hashtag   = 8   per followed hashtag that matches the post
  decay     = base * e^(-hours_since_post / 48)   (half-life ~33 h)
  final     = decay + interest + following + hashtag

Run periodically (e.g. every hour via cron / celery beat):
    python manage.py compute_feed_scores
"""
import math
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from django.db import transaction

from discussions.models import Post, PostAction, Comment, Debate, FeedScore, HashtagFollow
from users.models import Follow


class Command(BaseCommand):
    help = 'Recompute personalised feed scores for all active users'

    def add_arguments(self, parser):
        parser.add_argument('--hours', type=int, default=72,
                            help='Only score posts published within this many hours (default 72)')
        parser.add_argument('--users', type=int, default=0,
                            help='Limit to N most-recently-active users (0 = all)')

    def handle(self, *args, **options):
        cutoff = timezone.now() - timezone.timedelta(hours=options['hours'])
        posts = list(
            Post.objects.filter(
                created_at__gte=cutoff,
                is_draft=False,
                is_deleted_by_moderation=False,
                audience='public',
            ).only('id', 'user_id', 'category', 'hashtags', 'created_at', 'is_hot')
        )
        if not posts:
            self.stdout.write('No recent posts to score.')
            return

        # Precompute engagement signals per post
        likes_map = {
            r['post_id']: r['c']
            for r in PostAction.objects.filter(action='like', post__in=posts).values('post_id').annotate(c=__import__('django.db.models', fromlist=['Count']).Count('id'))
        }
        saves_map = {
            r['post_id']: r['c']
            for r in PostAction.objects.filter(action='save', post__in=posts).values('post_id').annotate(c=__import__('django.db.models', fromlist=['Count']).Count('id'))
        }
        comments_map = {
            r['post_id']: r['c']
            for r in Comment.objects.filter(post__in=posts).values('post_id').annotate(c=__import__('django.db.models', fromlist=['Count']).Count('id'))
        }
        debates_map = {
            r['post_id']: r['c']
            for r in Debate.objects.filter(post__in=posts).values('post_id').annotate(c=__import__('django.db.models', fromlist=['Count']).Count('id'))
        }

        user_qs = User.objects.all()
        if options['users']:
            user_qs = user_qs.order_by('-profile__last_seen')[:options['users']]

        now = timezone.now()
        batch = []
        BATCH_SIZE = 500

        for user in user_qs.iterator():
            try:
                interests = set(user.profile.interested_categories or [])
            except Exception:
                interests = set()

            followed_ids = set(
                Follow.objects.filter(follower=user).values_list('following_id', flat=True)
            )
            followed_hashtags = set(
                HashtagFollow.objects.filter(user=user).values_list('hashtag__lower', flat=True)
                if False else  # HashtagFollow may store name differently
                HashtagFollow.objects.filter(user=user).values_list('hashtag', flat=True)
            )

            for post in posts:
                hours_old = max(0, (now - post.created_at).total_seconds() / 3600)

                likes = likes_map.get(post.id, 0)
                saves = saves_map.get(post.id, 0)
                cmts = comments_map.get(post.id, 0)
                debates = debates_map.get(post.id, 0)
                hot_bonus = 10 if post.is_hot else 0

                base = likes * 1 + cmts * 2 + saves * 3 + debates * 5 + hot_bonus
                decay = base * math.exp(-hours_old / 48)

                interest_bonus = 15 if post.category in interests else 0
                following_bonus = 20 if post.user_id in followed_ids else 0

                hashtag_bonus = 0
                if followed_hashtags:
                    post_tags = set(t.strip().lower() for t in (post.hashtags or '').split(',') if t.strip())
                    hashtag_bonus = 8 * len(post_tags & followed_hashtags)

                score = round(decay + interest_bonus + following_bonus + hashtag_bonus, 4)
                batch.append(FeedScore(user=user, post=post, score=score))

                if len(batch) >= BATCH_SIZE:
                    self._flush(batch)
                    batch = []

        if batch:
            self._flush(batch)

        self.stdout.write(self.style.SUCCESS('Feed scores computed.'))

    @transaction.atomic
    def _flush(self, batch):
        FeedScore.objects.bulk_create(
            batch,
            update_conflicts=True,
            unique_fields=['user', 'post'],
            update_fields=['score', 'computed_at'],
        )
