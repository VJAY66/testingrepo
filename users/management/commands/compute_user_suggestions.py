"""
'People You May Know' suggestions — Instagram-style.

Scoring:
  mutual_follows   → +10 per shared mutual follower
  shared_interest  → +5  per shared interested category
  shared_hashtag   → +3  per shared followed hashtag

Run periodically (e.g. daily):
    python manage.py compute_user_suggestions
"""
from collections import defaultdict
from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.db import transaction

from users.models import Follow, UserSuggestion
from discussions.models import HashtagFollow


class Command(BaseCommand):
    help = 'Recompute People You May Know suggestions'

    def add_arguments(self, parser):
        parser.add_argument('--top', type=int, default=20,
                            help='Max suggestions to keep per user (default 20)')

    def handle(self, *args, **options):
        top_n = options['top']

        # Build follow map: user_id → set of following_ids
        follow_map = defaultdict(set)
        for f in Follow.objects.values('follower_id', 'following_id'):
            follow_map[f['follower_id']].add(f['following_id'])

        # Build interest map: user_id → set of categories
        interest_map = {}
        for user in User.objects.select_related('profile').iterator():
            try:
                interest_map[user.id] = set(user.profile.interested_categories or [])
            except Exception:
                interest_map[user.id] = set()

        # Build hashtag map: user_id → set of hashtags
        hashtag_map = defaultdict(set)
        for hf in HashtagFollow.objects.values('user_id', 'hashtag'):
            hashtag_map[hf['user_id']].add(hf['hashtag'])

        all_user_ids = list(follow_map.keys()) + [u.id for u in User.objects.all()]
        all_user_ids = list(set(all_user_ids))

        batch = []
        BATCH_SIZE = 300

        for uid in all_user_ids:
            following = follow_map.get(uid, set())
            blocked = set()  # could add UserBlock lookup here

            # Candidate pool: 2nd-degree connections
            candidates = defaultdict(float)
            for fid in following:
                for fof in follow_map.get(fid, set()):
                    if fof != uid and fof not in following and fof not in blocked:
                        candidates[fof] += 10.0  # mutual connection bonus

            # Interest bonus
            my_interests = interest_map.get(uid, set())
            for other_id, other_interests in interest_map.items():
                if other_id == uid or other_id in following:
                    continue
                shared = len(my_interests & other_interests)
                if shared:
                    candidates[other_id] += shared * 5.0

            # Hashtag bonus
            my_hashtags = hashtag_map.get(uid, set())
            for other_id, other_hashtags in hashtag_map.items():
                if other_id == uid or other_id in following:
                    continue
                shared = len(my_hashtags & other_hashtags)
                if shared:
                    candidates[other_id] += shared * 3.0

            # Pick top N
            top = sorted(candidates.items(), key=lambda x: -x[1])[:top_n]
            for suggested_id, score in top:
                # Determine reason
                my_following = follow_map.get(uid, set())
                mutual_count = len(my_following & follow_map.get(suggested_id, set()))
                shared_cats = len(my_interests & interest_map.get(suggested_id, set()))
                shared_tags = len(my_hashtags & hashtag_map.get(suggested_id, set()))

                if mutual_count >= shared_cats and mutual_count >= shared_tags:
                    reason = UserSuggestion.REASON_MUTUAL
                    detail = f'{mutual_count} mutual connection{"s" if mutual_count != 1 else ""}'
                elif shared_cats >= shared_tags:
                    reason = UserSuggestion.REASON_INTEREST
                    detail = f'{shared_cats} shared interest{"s" if shared_cats != 1 else ""}'
                else:
                    reason = UserSuggestion.REASON_HASHTAG
                    detail = f'{shared_tags} shared hashtag{"s" if shared_tags != 1 else ""}'

                try:
                    user_obj = User.objects.get(id=uid)
                    suggested_obj = User.objects.get(id=suggested_id)
                except User.DoesNotExist:
                    continue

                batch.append(UserSuggestion(
                    user=user_obj,
                    suggested_user=suggested_obj,
                    reason=reason,
                    reason_detail=detail,
                    score=round(score, 2),
                ))

                if len(batch) >= BATCH_SIZE:
                    self._flush(batch)
                    batch = []

        if batch:
            self._flush(batch)

        self.stdout.write(self.style.SUCCESS('User suggestions computed.'))

    @transaction.atomic
    def _flush(self, batch):
        UserSuggestion.objects.bulk_create(
            batch,
            update_conflicts=True,
            unique_fields=['user', 'suggested_user'],
            update_fields=['reason', 'reason_detail', 'score', 'computed_at'],
        )
