from django.core.management.base import BaseCommand
from django.contrib.auth.models import User
from django.utils import timezone
from collections import defaultdict


class Command(BaseCommand):
    help = 'Compute People You May Know suggestions for all active users'

    def add_arguments(self, parser):
        parser.add_argument('--days', type=int, default=30,
                            help='Only process users active in the last N days (default 30)')
        parser.add_argument('--limit', type=int, default=20,
                            help='Max suggestions stored per user (default 20)')

    def handle(self, *args, **options):
        from users.models import Follow, UserBlock, UserSuggestion, Profile
        from discussions.models import Post

        days = options['days']
        limit = options['limit']
        cutoff = timezone.now() - timezone.timedelta(days=days)

        # Only consider recently-active users
        active_user_ids = list(
            User.objects.filter(last_login__gte=cutoff).values_list('id', flat=True)
        )
        self.stdout.write(f'Processing {len(active_user_ids)} active users…')

        # Pre-load all follow pairs for fast lookup
        all_follows = list(Follow.objects.values_list('follower_id', 'following_id'))
        following_map = defaultdict(set)   # user_id -> set of user_ids they follow
        followers_map = defaultdict(set)   # user_id -> set of user_ids who follow them
        for follower_id, following_id in all_follows:
            following_map[follower_id].add(following_id)
            followers_map[following_id].add(follower_id)

        # Pre-load all blocks
        blocked_pairs = set(UserBlock.objects.values_list('blocker_id', 'blocked_id'))

        # Pre-load interest categories
        interest_map = {}   # user_id -> set of categories
        for profile in Profile.objects.exclude(interested_categories=[]).exclude(interested_categories=None):
            cats = profile.interested_categories
            if isinstance(cats, list) and cats:
                interest_map[profile.user_id] = set(cats)

        # Pre-load hashtag usage (user_id -> set of tags)
        hashtag_map = defaultdict(set)
        for post in Post.objects.filter(is_draft=False).exclude(hashtags='').values('user_id', 'hashtags'):
            raw = post['hashtags'] or ''
            tags = {t.lstrip('#').lower() for t in raw.split() if t.startswith('#')}
            hashtag_map[post['user_id']].update(tags)

        total_created = total_updated = 0

        for user_id in active_user_ids:
            already_following = following_map.get(user_id, set())
            exclude_ids = already_following | {user_id}
            blocked_by_me = {b for blocker, b in blocked_pairs if blocker == user_id}
            blocking_me = {blocker for blocker, b in blocked_pairs if b == user_id}
            exclude_ids |= blocked_by_me | blocking_me

            scores = defaultdict(lambda: {'score': 0.0, 'reason': '', 'detail': ''})

            # ── Mutual follows (2nd-degree connections) ──────────────────────
            for followed_id in already_following:
                for candidate_id in following_map.get(followed_id, set()):
                    if candidate_id in exclude_ids:
                        continue
                    scores[candidate_id]['score'] += 2.0
                    scores[candidate_id]['reason'] = UserSuggestion.REASON_MUTUAL
                    mutuals = len(already_following & followers_map.get(candidate_id, set()))
                    scores[candidate_id]['detail'] = f'{mutuals} mutual connection{"s" if mutuals != 1 else ""}'

            # ── Shared interests ─────────────────────────────────────────────
            my_cats = interest_map.get(user_id, set())
            if my_cats:
                for cand_id, cand_cats in interest_map.items():
                    if cand_id in exclude_ids:
                        continue
                    shared = my_cats & cand_cats
                    if shared:
                        bonus = len(shared) * 1.5
                        if bonus > scores[cand_id]['score']:
                            scores[cand_id]['reason'] = UserSuggestion.REASON_INTEREST
                            scores[cand_id]['detail'] = ', '.join(sorted(shared)[:2])
                        scores[cand_id]['score'] += bonus

            # ── Shared hashtags ──────────────────────────────────────────────
            my_tags = hashtag_map.get(user_id, set())
            if my_tags:
                for cand_id, cand_tags in hashtag_map.items():
                    if cand_id in exclude_ids:
                        continue
                    shared = my_tags & cand_tags
                    if shared:
                        bonus = len(shared) * 1.0
                        if bonus > scores[cand_id]['score']:
                            scores[cand_id]['reason'] = UserSuggestion.REASON_HASHTAG
                            scores[cand_id]['detail'] = '#' + ', #'.join(sorted(shared)[:2])
                        scores[cand_id]['score'] += bonus

            # Keep top `limit` candidates
            top = sorted(scores.items(), key=lambda x: -x[1]['score'])[:limit]

            try:
                user_obj = User.objects.get(id=user_id)
            except User.DoesNotExist:
                continue

            for cand_id, data in top:
                try:
                    cand_obj = User.objects.get(id=cand_id)
                except User.DoesNotExist:
                    continue
                obj, created = UserSuggestion.objects.update_or_create(
                    user=user_obj,
                    suggested_user=cand_obj,
                    defaults={
                        'score': data['score'],
                        'reason': data['reason'] or UserSuggestion.REASON_MUTUAL,
                        'reason_detail': data['detail'][:120],
                    },
                )
                if created:
                    total_created += 1
                else:
                    total_updated += 1

        self.stdout.write(self.style.SUCCESS(
            f'Done. Created {total_created}, updated {total_updated} suggestions.'
        ))
