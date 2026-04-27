from django.db import models
from django.contrib.auth.models import User
import re

CATEGORY_CHOICES = [
    ('Astrology', 'Astrology'),
    ('Beauty', 'Beauty'),
    ('Business', 'Business'),
    ('Education', 'Education'),
    ('Entertainment', 'Entertainment'),
    ('Fashion', 'Fashion'),
    ('Food', 'Food'),
    ('Gadgets', 'Gadgets'),
    ('Health', 'Health'),
    ('History', 'History'),
    ('Investment', 'Investment'),
    ('Medicenes', 'Medicenes'),
    ('Music', 'Music'),
    ('Painting', 'Painting'),
    ('Photography', 'Photography'),
    ('Politics', 'Politics'),
    ('Relationships', 'Relationships'),
    ('Science', 'Science'),
    ('Spirituality', 'Spirituality'),
    ('Sports', 'Sports'),
    ('Technology', 'Technology'),
    ('Travel', 'Travel'),
    ('Vehicles', 'Vehicles'),
    ('Others', 'Others'),
]

class Post(models.Model):
    HASHTAG_MAX_LENGTH = 40
    id = models.CharField(max_length=36, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    title = models.CharField(max_length=255)
    content = models.TextField()
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    hashtags = models.TextField(blank=True, default='', help_text='Comma-separated hashtags')
    is_edited = models.BooleanField(default=False)
    is_deleted_by_moderation = models.BooleanField(default=False, help_text='Automatically deleted by moderation')
    is_flagged = models.BooleanField(default=False, help_text='Flagged by moderation system')
    moderation_reason = models.TextField(blank=True, default='', help_text='Reason for moderation action')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    @staticmethod
    def parse_hashtags(raw_value, max_tags=5):
        """Parse hashtags from free text like '#ai #ml,python' into clean unique tags."""
        if not raw_value:
            return []

        # Accept both comma-separated and space-separated tags, with or without '#'.
        tokens = re.findall(r'#?([A-Za-z0-9_]+)', str(raw_value).lower())
        unique = []
        seen = set()
        for token in tokens:
            if token and len(token) <= Post.HASHTAG_MAX_LENGTH and token not in seen:
                seen.add(token)
                unique.append(token)
            if len(unique) >= max_tags:
                break
        return unique
    
    def get_hashtags_list(self):
        """Return hashtags as a clean list even for legacy text formats."""
        return Post.parse_hashtags(self.hashtags, max_tags=20)

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
    reply_to = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, related_name='replies')
    content = models.TextField(blank=True)
    vote_type = models.CharField(max_length=10, choices=VOTE_CHOICES)
    likes = models.IntegerField(default=0)
    dislikes = models.IntegerField(default=0)
    is_edited = models.BooleanField(default=False)
    is_deleted_by_moderation = models.BooleanField(default=False, help_text='Automatically deleted by moderation')
    is_flagged = models.BooleanField(default=False, help_text='Flagged by moderation system')
    moderation_reason = models.TextField(blank=True, default='', help_text='Reason for moderation action')
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


class PostView(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_views')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='views')
    viewed_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} viewed post '{self.post.title}'"

    class Meta:
        ordering = ['-viewed_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'post'], name='unique_post_view'),
        ]


class PostAction(models.Model):
    ACTION_CHOICES = [
        ('like', 'Like'),
        ('save', 'Save'),
        ('repost', 'Repost'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_actions')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='actions')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} {self.action}d post '{self.post.title}'"

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'post', 'action'], name='unique_post_action'),
        ]


class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('post_comment', 'New Comment on Followed Post'),
        ('post_activity', 'Active Conversation on Followed Post'),
        ('moderation_alert', 'Moderator Alert'),
        ('moderation_warning', 'Moderation Warning'),
        ('author_comment', 'New Comment on Your Post'),
        ('author_debate', 'New Debate on Your Post'),
        ('author_repost', 'Your Post was Reposted'),
        ('author_save', 'Your Post was Saved'),
        ('mention', 'You Were Mentioned'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='notifications')
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    message = models.TextField()
    is_read = models.BooleanField(default=False)
    count = models.PositiveIntegerField(default=1)
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
    is_edited = models.BooleanField(default=False)
    is_deleted_by_moderation = models.BooleanField(default=False, help_text='Automatically deleted by moderation')
    is_flagged = models.BooleanField(default=False, help_text='Flagged by moderation system')
    moderation_reason = models.TextField(blank=True, default='', help_text='Reason for moderation action')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Message by {self.sender.username} in debate {self.debate_id}"

    class Meta:
        ordering = ['created_at']


class DebateMessageReaction(models.Model):
    REACTION_CHOICES = [
        ('like', 'Like'),
        ('dislike', 'Dislike'),
    ]

    message = models.ForeignKey(DebateMessage, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='debate_message_reactions')
    reaction = models.CharField(max_length=10, choices=REACTION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.user.username} {self.reaction} on debate message {self.message_id}"

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['message', 'user'], name='unique_debate_message_reaction_per_user'),
        ]


class PostEditHistory(models.Model):
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='edit_history')
    original_title = models.CharField(max_length=255)
    original_content = models.TextField(blank=True)
    edited_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Edit of post '{self.post_id}' at {self.edited_at}"

    class Meta:
        ordering = ['-edited_at']


class CommentEditHistory(models.Model):
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='edit_history')
    original_content = models.TextField()
    edited_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Edit of comment '{self.comment_id}' at {self.edited_at}"

    class Meta:
        ordering = ['-edited_at']


class DebateMessageEditHistory(models.Model):
    message = models.ForeignKey(DebateMessage, on_delete=models.CASCADE, related_name='edit_history')
    original_content = models.TextField()
    edited_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Edit of message '{self.message_id}' at {self.edited_at}"

    class Meta:
        ordering = ['-edited_at']


class DebateMessageReport(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('dismissed', 'Dismissed'),
        ('actioned', 'Actioned'),
    ]

    debate = models.ForeignKey(Debate, on_delete=models.CASCADE, related_name='message_reports')
    message = models.ForeignKey(DebateMessage, on_delete=models.CASCADE, related_name='reports')
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reported_debate_messages')
    reported_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='debate_message_reports_against')
    reason = models.CharField(max_length=40, default='abusive_language')
    details = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_debate_message_reports')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    resolution_note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Report #{self.id} on message {self.message_id} ({self.status})"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['message', 'reporter'], name='unique_debate_message_report_per_reporter'),
        ]


class ProfileReport(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('reviewed', 'Reviewed'),
        ('dismissed', 'Dismissed'),
    ]

    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='profile_reports_submitted')
    reported_user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='profile_reports_received')
    reason = models.CharField(max_length=40, default='profile_concern')
    details = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Profile report #{self.id} on {self.reported_user.username} ({self.status})"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['reporter', 'reported_user'], name='unique_profile_report_per_reporter'),
        ]


class ObserverVote(models.Model):
    """Spectators vote on who argued best in a completed debate."""
    SIDE_CHOICES = [('yes', 'Yes'), ('no', 'No')]

    debate = models.ForeignKey(Debate, on_delete=models.CASCADE, related_name='observer_votes')
    voter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='observer_votes_cast')
    winner_side = models.CharField(max_length=10, choices=SIDE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.voter.username} voted '{self.winner_side}' wins debate {self.debate_id}"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['debate', 'voter'], name='unique_observer_vote_per_debate'),
        ]


class CommentReport(models.Model):
    """User-submitted report on a post comment."""
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('dismissed', 'Dismissed'),
        ('actioned', 'Actioned'),
    ]
    REASON_CHOICES = [
        ('abusive_language', 'Abusive language'),
        ('spam', 'Spam or misleading'),
        ('misinformation', 'Misinformation'),
        ('harassment', 'Harassment'),
        ('other', 'Other'),
    ]

    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='reports')
    reporter = models.ForeignKey(User, on_delete=models.CASCADE, related_name='comment_reports_submitted')
    reason = models.CharField(max_length=30, choices=REASON_CHOICES, default='abusive_language')
    details = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Report #{self.id} on comment {self.comment_id} ({self.status})"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['comment', 'reporter'], name='unique_comment_report_per_reporter'),
        ]


class HashtagFollow(models.Model):
    """User follows a hashtag — shows up in their hashtag feed."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='hashtag_follows')
    tag = models.CharField(max_length=40, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} follows #{self.tag}"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'tag'], name='unique_hashtag_follow'),
        ]
