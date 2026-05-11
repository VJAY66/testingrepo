from django.db import models
from django.contrib.auth.models import User

TRUST_BADGE_MAP = {
    'verified': ('✓', '#0ea5e9', 'Verified'),
    'expert':   ('★', '#f59e0b', 'Expert'),
    'contributor': ('◆', '#8b5cf6', 'Contributor'),
    'member':   ('●', '#22c55e', 'Member'),
    'new':      ('', '', ''),
}

DEFAULT_NOTIFICATION_PREFS = {
    'follow': {'email': True, 'inapp': True},
    'mention': {'email': True, 'inapp': True},
    'reply': {'email': True, 'inapp': True},
    'debate_request': {'email': True, 'inapp': True},
    'poll_closed': {'email': True, 'inapp': True},
    'weekly_digest': {'email': True},
    'moderation_warning': {'email': False, 'inapp': True},
}

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    username = models.CharField(max_length=30, unique=True)
    avatar_url = models.URLField(null=True, blank=True)
    profile_picture = models.ImageField(upload_to='profile_pictures/', null=True, blank=True)
    bio = models.TextField(blank=True, default='', max_length=280)
    website = models.URLField(blank=True, default='')
    interested_categories = models.JSONField(default=list, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True, db_index=True)
    is_verified = models.BooleanField(default=False, help_text='Manually verified by a moderator')
    notification_prefs = models.JSONField(default=dict, blank=True, help_text='Per-type notification opt-in settings')
    theme = models.CharField(
        max_length=10,
        default='light',
        choices=[('light', 'Light'), ('dark', 'Dark')],
        help_text='User preferred color theme'
    )
    reputation_score = models.IntegerField(default=0, db_index=True, help_text='Computed reputation from likes, answers, debates')
    streak_days = models.PositiveIntegerField(default=0, help_text='Current consecutive days of activity')
    last_activity_date = models.DateField(null=True, blank=True, help_text='Last date the user posted or commented')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.username

    @property
    def get_picture_url(self):
        """Return the best available picture URL: uploaded file first, then external URL."""
        if self.profile_picture:
            return self.profile_picture.url
        return self.avatar_url or ''

    @property
    def followers_count(self):
        return self.user.follower_links.count()

    @property
    def following_count(self):
        return self.user.following_links.count()

    @property
    def posts_count(self):
        return self.user.posts.count()

    @property
    def trust_level(self):
        if self.is_verified:
            return 'verified'
        posts = self.user.posts.count()
        debates = self.user.debate_participations.filter(debate__status='completed').count()
        if posts >= 50 or debates >= 20:
            return 'expert'
        if posts >= 10 or debates >= 5:
            return 'contributor'
        if posts >= 1:
            return 'member'
        return 'new'

    @property
    def trust_badge(self):
        return TRUST_BADGE_MAP.get(self.trust_level, ('', '', ''))

    class Meta:
        ordering = ['-created_at']


class Follow(models.Model):
    follower = models.ForeignKey(User, on_delete=models.CASCADE, related_name='following_links')
    following = models.ForeignKey(User, on_delete=models.CASCADE, related_name='follower_links')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.follower.username} -> {self.following.username}"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['follower', 'following'], name='unique_follow_pair'),
        ]


class UserBlock(models.Model):
    """General-purpose user block — hides content and prevents all interaction."""
    blocker = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocking')
    blocked = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocked_by')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.blocker.username} blocked {self.blocked.username}"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['blocker', 'blocked'], name='unique_user_block'),
        ]


class SaveCollection(models.Model):
    """Named collection of saved posts, owned by a user."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='save_collections')
    name = models.CharField(max_length=60)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} / {self.name}"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'name'], name='unique_collection_per_user'),
        ]


class CollectionItem(models.Model):
    """A post bookmarked into a SaveCollection."""
    collection = models.ForeignKey(SaveCollection, on_delete=models.CASCADE, related_name='items')
    post = models.ForeignKey('discussions.Post', on_delete=models.CASCADE, related_name='collection_items')
    tag = models.CharField(max_length=40, blank=True, default='', help_text='Optional label for this bookmark')
    added_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.collection.name} / {self.post_id}"

    class Meta:
        ordering = ['-added_at']
        constraints = [
            models.UniqueConstraint(fields=['collection', 'post'], name='unique_collection_item'),
        ]


class MutedKeyword(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='muted_keywords')
    keyword = models.CharField(max_length=60, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} mutes '{self.keyword}'"

    class Meta:
        ordering = ['keyword']
        constraints = [
            models.UniqueConstraint(fields=['user', 'keyword'], name='unique_muted_keyword'),
        ]


ACHIEVEMENT_DEFS = [
    ('first_post',      '✍️',  'First Post',         'Published your first discussion'),
    ('debate_starter',  '⚔️',  'Debate Starter',     'Started 5 debates'),
    ('top_voice',       '🔥',  'Top Voice',          'Received 50 likes across all posts'),
    ('helpful',         '🌟',  'Helpful',            'Had a Best Answer marked'),
    ('poll_master',     '📊',  'Poll Master',        'Created 10 polls'),
    ('verified_voice',  '✓',   'Verified Voice',     'Account verified by a moderator'),
    ('contributor',     '💎',  'Contributor',        'Posted 10 discussions'),
    ('veteran',         '🏆',  'Veteran',            'Active for 30+ days'),
    ('rep_100',        '⭐',  'Rising Star',       'Earned 100 reputation points'),
    ('rep_500',        '🌠',  'Shining Star',      'Earned 500 reputation points'),
    ('streak_7',       '📅',  'Week Warrior',      'Maintained a 7-day activity streak'),
    ('streak_30',      '🗓️',  'Monthly Legend',    'Maintained a 30-day activity streak'),
    ('challenger',     '🎯',  'Challenger',        'Entered a community challenge'),
    ('hot_author',     '♨️',  'Hot Author',        'Had a post flagged as Hot'),
    ('debate_winner',  '🥇',  'Debate Winner',     'Won 3 or more debates'),
]


class Achievement(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='achievements')
    code = models.CharField(max_length=40, db_index=True)
    awarded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-awarded_at']
        constraints = [models.UniqueConstraint(fields=['user', 'code'], name='unique_user_achievement')]


class Endorsement(models.Model):
    """One user endorses a topic/skill on another user's profile."""
    endorser = models.ForeignKey(User, on_delete=models.CASCADE, related_name='given_endorsements')
    endorsed = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_endorsements')
    topic = models.CharField(max_length=60)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['endorser', 'endorsed', 'topic'], name='unique_endorsement')]
        ordering = ['-created_at']


class LoginAttempt(models.Model):
    SOURCE_WEB = 'web'
    SOURCE_API = 'api'
    SOURCE_CHOICES = [
        (SOURCE_WEB, 'Web'),
        (SOURCE_API, 'API'),
    ]

    username = models.CharField(max_length=150, db_index=True)
    ip_address = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    user_agent = models.CharField(max_length=255, blank=True)
    source = models.CharField(max_length=10, choices=SOURCE_CHOICES, default=SOURCE_WEB, db_index=True)
    successful = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    def __str__(self):
        outcome = 'ok' if self.successful else 'fail'
        return f"{self.username} [{self.source}] {outcome}"

    class Meta:
        ordering = ['-created_at']


class CloseFriend(models.Model):
    """User marks another user as a Close Friend — they see close_friends audience posts."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='close_friends_list')
    friend = models.ForeignKey(User, on_delete=models.CASCADE, related_name='in_close_friends_of')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} → {self.friend.username} (close friend)"

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'friend'], name='unique_close_friend')]
        ordering = ['-created_at']


class UserSuggestion(models.Model):
    """Pre-computed 'People You May Know' suggestions."""
    REASON_MUTUAL = 'mutual_follow'
    REASON_INTEREST = 'shared_interest'
    REASON_HASHTAG = 'shared_hashtag'
    REASON_CHOICES = [
        (REASON_MUTUAL, 'Mutual Follows'),
        (REASON_INTEREST, 'Shared Interests'),
        (REASON_HASHTAG, 'Shared Hashtags'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='suggestions_for')
    suggested_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='suggested_to')
    reason = models.CharField(max_length=20, choices=REASON_CHOICES, default=REASON_MUTUAL)
    reason_detail = models.CharField(max_length=120, blank=True, default='')
    score = models.FloatField(default=0.0)
    computed_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Suggest {self.suggested_user.username} to {self.user.username}"

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'suggested_user'], name='unique_user_suggestion')]
        ordering = ['-score']


class PushSubscription(models.Model):
    """Browser/mobile push notification subscription (Web Push API)."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='push_subscriptions')
    endpoint = models.URLField(max_length=800, unique=True)
    p256dh = models.TextField()
    auth = models.TextField()
    user_agent = models.CharField(max_length=300, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"Push({self.user.username}) {self.endpoint[:60]}"
