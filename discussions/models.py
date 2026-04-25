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


class Poll(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='polls')
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True, default='')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    hashtags = models.TextField(blank=True, default='')
    is_active = models.BooleanField(default=True)
    is_deleted_by_moderation = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_hashtags_list(self):
        if not self.hashtags:
            return []
        return [t.strip().lstrip('#') for t in self.hashtags.split(',') if t.strip()]

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-created_at']


class PollOption(models.Model):
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='options')
    text = models.CharField(max_length=150)
    order = models.PositiveSmallIntegerField(default=0)

    def __str__(self):
        return f"{self.poll.title} — {self.text}"

    class Meta:
        ordering = ['order']


class PollVote(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='poll_votes')
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='votes')
    option = models.ForeignKey(PollOption, on_delete=models.CASCADE, related_name='votes')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} voted '{self.option.text}' on {self.poll.title}"

    class Meta:
        ordering = ['-created_at']
        constraints = [
            models.UniqueConstraint(fields=['user', 'poll'], name='unique_poll_vote_per_user'),
        ]


class PollComment(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='poll_comments')
    option = models.ForeignKey(PollOption, on_delete=models.CASCADE, related_name='comments')
    content = models.TextField(blank=True)
    likes = models.IntegerField(default=0)
    dislikes = models.IntegerField(default=0)
    is_edited = models.BooleanField(default=False)
    is_deleted_by_moderation = models.BooleanField(default=False)
    is_flagged = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"PollComment on {self.poll.title} — {self.option.text}"

    class Meta:
        ordering = ['created_at']
        constraints = [
            models.UniqueConstraint(fields=['poll', 'user'], name='unique_poll_comment_per_user'),
        ]


class PollCommentReaction(models.Model):
    REACTION_CHOICES = [('like', 'Like'), ('dislike', 'Dislike')]
    comment = models.ForeignKey(PollComment, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='poll_comment_reactions')
    reaction = models.CharField(max_length=10, choices=REACTION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['comment', 'user'], name='unique_poll_comment_reaction'),
        ]


class Question(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='questions')
    title = models.CharField(max_length=255)
    content = models.TextField(blank=True, default='')
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    hashtags = models.TextField(blank=True, default='')
    answer_count = models.IntegerField(default=0)
    best_answer = models.ForeignKey('Answer', on_delete=models.SET_NULL, null=True, blank=True, related_name='best_for_question')
    is_deleted_by_moderation = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_hashtags_list(self):
        if not self.hashtags:
            return []
        return [t.strip().lstrip('#') for t in self.hashtags.split(',') if t.strip()]

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-created_at']


class Answer(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name='answers')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='answers')
    content = models.TextField()
    upvotes = models.IntegerField(default=0)
    downvotes = models.IntegerField(default=0)
    is_deleted_by_moderation = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Answer by {self.user.username} on {self.question.title}"

    class Meta:
        ordering = ['-upvotes', 'created_at']


class AnswerVote(models.Model):
    VOTE_CHOICES = [('up', 'Upvote'), ('down', 'Downvote')]
    answer = models.ForeignKey(Answer, on_delete=models.CASCADE, related_name='votes')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='answer_votes')
    vote = models.CharField(max_length=5, choices=VOTE_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['answer', 'user'], name='unique_answer_vote_per_user'),
        ]


class Review(models.Model):
    SUBJECT_TYPE_CHOICES = [
        ('Movie', 'Movie'),
        ('TV Show', 'TV Show'),
        ('Book', 'Book'),
        ('Music / Album', 'Music / Album'),
        ('Product', 'Product'),
        ('Place', 'Place'),
        ('Restaurant', 'Restaurant'),
        ('App / Game', 'App / Game'),
        ('Person', 'Person'),
        ('Other', 'Other'),
    ]

    id = models.CharField(max_length=36, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='reviews')
    subject = models.CharField(max_length=255)
    subject_type = models.CharField(max_length=50, choices=SUBJECT_TYPE_CHOICES)
    rating = models.PositiveSmallIntegerField()
    content = models.TextField()
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES)
    hashtags = models.TextField(blank=True, default='')
    agree_count = models.IntegerField(default=0)
    disagree_count = models.IntegerField(default=0)
    is_deleted_by_moderation = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def get_hashtags_list(self):
        if not self.hashtags:
            return []
        return [t.strip().lstrip('#') for t in self.hashtags.split(',') if t.strip()]

    def __str__(self):
        return f"{self.user.username}'s review of {self.subject}"

    class Meta:
        ordering = ['-created_at']


class ReviewReaction(models.Model):
    REACTION_CHOICES = [('agree', 'Agree'), ('disagree', 'Disagree')]
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='review_reactions')
    reaction = models.CharField(max_length=10, choices=REACTION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['review', 'user'], name='unique_review_reaction_per_user'),
        ]


class ReviewComment(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    review = models.ForeignKey(Review, on_delete=models.CASCADE, related_name='comments')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='review_comments')
    content = models.TextField()
    likes = models.IntegerField(default=0)
    dislikes = models.IntegerField(default=0)
    is_deleted_by_moderation = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"ReviewComment on {self.review.subject} by {self.user.username}"

    class Meta:
        ordering = ['created_at']


class ReviewCommentReaction(models.Model):
    REACTION_CHOICES = [('like', 'Like'), ('dislike', 'Dislike')]
    comment = models.ForeignKey(ReviewComment, on_delete=models.CASCADE, related_name='reactions')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='review_comment_reactions')
    reaction = models.CharField(max_length=10, choices=REACTION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-updated_at']
        constraints = [
            models.UniqueConstraint(fields=['comment', 'user'], name='unique_review_comment_reaction'),
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
