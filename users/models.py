from django.db import models
from django.contrib.auth.models import User

TRUST_BADGE_MAP = {
    'verified': ('✓', '#0ea5e9', 'Verified'),
    'expert':   ('★', '#f59e0b', 'Expert'),
    'contributor': ('◆', '#8b5cf6', 'Contributor'),
    'member':   ('●', '#22c55e', 'Member'),
    'new':      ('', '', ''),
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
