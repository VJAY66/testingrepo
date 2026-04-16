from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

CATEGORY_CHOICES = [
    ('Technology', 'Technology'),
    ('Sports', 'Sports'),
    ('Science', 'Science'),
    ('History', 'History'),
    ('Politics', 'Politics'),
    ('Entertainment', 'Entertainment'),
    ('Health', 'Health'),
    ('Business', 'Business'),
    ('Gadgets', 'Gadgets'),
    ('Vehicles', 'Vehicles'),
    ('Education', 'Education'),
    ('Travel', 'Travel'),
    ('Food', 'Food'),
    ('Investment', 'Investment'),
    ('Astrology', 'Astrology'),
    ('Spirituality', 'Spirituality'),
    ('Others', 'Others'),
]

class Post(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    title = models.CharField(max_length=255)
    content = models.TextField()
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-created_at']

class Comment(models.Model):
    VOTE_CHOICES = [
        ('yes', 'Yes'),
        ('no', 'No'),
    ]
    
    id = models.CharField(max_length=36, primary_key=True)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='comments')
    content = models.TextField(blank=True)
    vote_type = models.CharField(max_length=10, choices=VOTE_CHOICES)
    likes = models.IntegerField(default=0)
    dislikes = models.IntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Comment on {self.post.title} - {self.vote_type}"

    class Meta:
        ordering = ['created_at']


class CommentReaction(models.Model):
    REACTION_CHOICES = [
        ('like', 'Like'),
        ('dislike', 'Dislike'),
    ]

    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='comment_reactions')
    reaction = models.CharField(max_length=10, choices=REACTION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} {self.reaction} on {self.comment_id}"

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['comment', 'user'], name='unique_comment_reaction_per_user'),
        ]

class Debate(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('completed', 'Completed'),
    ]
    
    id = models.CharField(max_length=36, primary_key=True)
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='debates')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='debates')
    initiator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='initiated_debates')
    target = models.ForeignKey(User, on_delete=models.CASCADE, related_name='target_debates')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    yes_supporters = models.PositiveIntegerField(default=0)
    no_supporters = models.PositiveIntegerField(default=0)
    end_controller = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='controlled_debates')
    end_controller_side = models.CharField(max_length=10, choices=Comment.VOTE_CHOICES, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Debate between {self.initiator.username} and {self.target.username}"

    class Meta:
        ordering = ['-created_at']


class DebateParticipant(models.Model):
    SIDE_CHOICES = [
        ('yes', 'Yes'),
        ('no', 'No'),
    ]

    debate = models.ForeignKey(Debate, on_delete=models.CASCADE, related_name='participants')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='debate_participations')
    side = models.CharField(max_length=10, choices=SIDE_CHOICES)
    is_active = models.BooleanField(default=True)
    is_banned = models.BooleanField(default=False)
    joined_at = models.DateTimeField(auto_now_add=True)
    left_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user.username} ({self.side}) in debate {self.debate_id}"

    class Meta:
        ordering = ['joined_at']
        constraints = [
            models.UniqueConstraint(fields=['debate', 'user'], name='unique_debate_participant'),
        ]


class CommentModeratorBlock(models.Model):
    comment_owner = models.ForeignKey(User, on_delete=models.CASCADE, related_name='moderated_blocks')
    blocked_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='blocked_by_comment_owners')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.comment_owner.username} blocked {self.blocked_user.username}"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['comment_owner', 'blocked_user'], name='unique_comment_owner_block'),
        ]


class PostFollow(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='followed_posts')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='followers')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} follows post '{self.post.title}'"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'post'], name='unique_post_follow'),
        ]


class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('post_comment', 'New Comment on Followed Post'),
        ('post_activity', 'Active Conversation on Followed Post'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notification for {self.user.username}: {self.message}"

    class Meta:
        ordering = ['-created_at']


class DebateMessage(models.Model):
    id = models.BigAutoField(primary_key=True)
    debate = models.ForeignKey(Debate, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='debate_messages')
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='replies')
    content = models.TextField()
    is_system = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Message by {self.sender.username} in debate {self.debate_id}"

    class Meta:
        ordering = ['created_at']
