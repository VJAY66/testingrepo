from django.db import models
from django.contrib.auth.models import User

class Profile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    username = models.CharField(max_length=30, unique=True)
    avatar_url = models.URLField(null=True, blank=True)
    profile_picture = models.ImageField(upload_to='profile_pictures/', null=True, blank=True)
    last_seen = models.DateTimeField(null=True, blank=True, db_index=True)
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
