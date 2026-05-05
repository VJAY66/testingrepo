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
    AUDIENCE_PUBLIC = 'public'
    AUDIENCE_FOLLOWERS = 'followers'
    AUDIENCE_CLOSE_FRIENDS = 'close_friends'
    AUDIENCE_CHOICES = [
        (AUDIENCE_PUBLIC, 'Public'),
        (AUDIENCE_FOLLOWERS, 'Followers Only'),
        (AUDIENCE_CLOSE_FRIENDS, 'Close Friends'),
    ]

    id = models.CharField(max_length=36, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='posts')
    title = models.CharField(max_length=255)
    content = models.TextField()
    category = models.CharField(max_length=50, choices=CATEGORY_CHOICES, db_index=True)
    hashtags = models.TextField(blank=True, default='')
    is_edited = models.BooleanField(default=False)
    is_deleted_by_moderation = models.BooleanField(default=False, help_text='Automatically deleted by moderation')
    is_flagged = models.BooleanField(default=False, help_text='Flagged by moderation system')
    moderation_reason = models.TextField(blank=True, default='', help_text='Reason for moderation action')
    is_draft = models.BooleanField(default=False, help_text='Saved draft, not yet published')
    is_pinned = models.BooleanField(default=False, help_text='Pinned to top of author profile')
    scheduled_for = models.DateTimeField(null=True, blank=True, help_text='Publish this draft automatically at this time')
    closes_at = models.DateTimeField(null=True, blank=True, help_text='Lock comments after this time')
    MOOD_CHOICES = [
        ('controversial', '🔥 Controversial'),
        ('educational',   '💡 Educational'),
        ('funny',         '😂 Funny'),
        ('mindblowing',   '🤯 Mind-blowing'),
        ('emotional',     '💔 Emotional'),
        ('news',          '📰 News'),
        ('unpopular',     '🙃 Unpopular Opinion'),
        ('hottake',       '☄️ Hot Take'),
    ]

    is_hot = models.BooleanField(default=False, db_index=True, help_text='Auto-flagged as rapidly gaining reactions')
    mood = models.CharField(max_length=20, choices=MOOD_CHOICES, blank=True, default='', db_index=True)
    audience = models.CharField(max_length=20, choices=AUDIENCE_CHOICES, default=AUDIENCE_PUBLIC, db_index=True)
    REPLY_EVERYONE = 'everyone'
    REPLY_FOLLOWERS = 'followers'
    REPLY_CLOSE_FRIENDS = 'close_friends'
    REPLY_NOBODY = 'nobody'
    REPLY_CHOICES = [
        (REPLY_EVERYONE, 'Everyone'),
        (REPLY_FOLLOWERS, 'Followers'),
        (REPLY_CLOSE_FRIENDS, 'Close Friends'),
        (REPLY_NOBODY, 'Nobody'),
    ]
    reply_restriction = models.CharField(max_length=20, choices=REPLY_CHOICES, default=REPLY_EVERYONE)
    quoted_post = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='quotes')
    reading_time_minutes = models.PositiveSmallIntegerField(default=1)
    word_count = models.PositiveIntegerField(default=0)
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

    def compute_reading_time(self):
        words = len((self.content or '').split())
        self.word_count = words
        self.reading_time_minutes = max(1, round(words / 200))

    def save(self, *args, **kwargs):
        self.compute_reading_time()
        super().save(*args, **kwargs)

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
    is_pinned = models.BooleanField(default=False, db_index=True, help_text='Post author pinned this comment')
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
        ('countered', 'Counter Proposed'),
        ('accepted', 'Accepted'),
        ('rejected', 'Rejected'),
        ('completed', 'Completed'),
    ]

    id = models.CharField(max_length=36, primary_key=True)
    comment = models.ForeignKey(Comment, on_delete=models.CASCADE, related_name='debates', null=True, blank=True)
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='debates', null=True, blank=True)
    poll_comment = models.ForeignKey('PollComment', on_delete=models.CASCADE, related_name='debates', null=True, blank=True)
    poll = models.ForeignKey('Poll', on_delete=models.CASCADE, related_name='debates', null=True, blank=True)
    initiator = models.ForeignKey(User, on_delete=models.CASCADE, related_name='initiated_debates')
    target = models.ForeignKey(User, on_delete=models.CASCADE, related_name='target_debates')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    yes_supporters = models.PositiveIntegerField(default=0)
    no_supporters = models.PositiveIntegerField(default=0)
    end_controller = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='controlled_debates')
    end_controller_side = models.CharField(max_length=10, choices=Comment.VOTE_CHOICES, blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    rematch_of = models.ForeignKey('self', null=True, blank=True, on_delete=models.SET_NULL, related_name='rematches')
    round_duration_minutes = models.PositiveSmallIntegerField(default=10, help_text='Minutes per debate round')
    round_ends_at = models.DateTimeField(null=True, blank=True, help_text='When the current round timer expires')
    outcome = models.CharField(max_length=10, blank=True, default='', help_text="'draw' if both agreed to mutual draw")
    draw_proposed_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='draw_proposals')
    counter_topic = models.CharField(max_length=200, blank=True, default='', help_text='Alternative topic proposed by the challenged user')
    counter_side = models.CharField(max_length=10, blank=True, default='', help_text='Side the challenger wants in the counter-proposal')

    @property
    def context_title(self):
        if self.post_id:
            return self.post.title
        if self.poll_id:
            return self.poll.title
        return ''

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
    last_read_message_id = models.BigIntegerField(default=0, help_text='ID of the last message this participant has read')

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
        ('hot', '🔥 Hot'),
        ('debatable', '🤔 Debatable'),
        ('agree', '👏 Agree'),
        ('surprising', '😮 Surprising'),
    ]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_actions')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='actions')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES, db_index=True)
    quote_content = models.TextField(blank=True, default='')
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
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='notifications', null=True, blank=True)
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES)
    message = models.TextField()
    is_read = models.BooleanField(default=False, db_index=True)
    count = models.PositiveIntegerField(default=1)
    actors = models.JSONField(default=list, blank=True, help_text='Usernames of users involved, for batched display')
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"Notification for {self.user.username}: {self.message}"

    class Meta:
        ordering = ['-created_at']


class DebateView(models.Model):
    """Tracks who is currently viewing a debate chat page (for live spectator count)."""
    debate = models.ForeignKey(Debate, on_delete=models.CASCADE, related_name='spectators')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='debate_views')
    last_seen = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['debate', 'user'], name='unique_debate_view')]


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
    expires_at = models.DateTimeField(null=True, blank=True, help_text='Auto-close this poll at this time. Leave blank for no expiry.')
    expiry_notified = models.BooleanField(default=False, help_text='Whether followers have been notified of poll closure')
    is_anonymous = models.BooleanField(default=False, help_text='Hide voter identities from results')
    allows_ranked_choice = models.BooleanField(default=False, help_text='Allow voters to rank options in order of preference')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    @property
    def is_expired(self):
        if not self.expires_at:
            return False
        from django.utils import timezone
        return timezone.now() >= self.expires_at

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


class PollAction(models.Model):
    ACTION_CHOICES = [('like', 'Like'), ('save', 'Save'), ('repost', 'Repost')]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='poll_actions')
    poll = models.ForeignKey('Poll', on_delete=models.CASCADE, related_name='actions')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['user', 'poll', 'action'], name='unique_poll_action')]


class PollFollow(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='followed_polls')
    poll = models.ForeignKey('Poll', on_delete=models.CASCADE, related_name='followers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['user', 'poll'], name='unique_poll_follow')]


class QuestionAction(models.Model):
    ACTION_CHOICES = [('like', 'Like'), ('save', 'Save'), ('repost', 'Repost')]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='question_actions')
    question = models.ForeignKey('Question', on_delete=models.CASCADE, related_name='actions')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['user', 'question', 'action'], name='unique_question_action')]


class QuestionFollow(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='followed_questions')
    question = models.ForeignKey('Question', on_delete=models.CASCADE, related_name='followers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['user', 'question'], name='unique_question_follow')]


class ReviewAction(models.Model):
    ACTION_CHOICES = [('like', 'Like'), ('save', 'Save'), ('repost', 'Repost')]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='review_actions')
    review = models.ForeignKey('Review', on_delete=models.CASCADE, related_name='actions')
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['user', 'review', 'action'], name='unique_review_action')]


class ReviewFollow(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='followed_reviews')
    review = models.ForeignKey('Review', on_delete=models.CASCADE, related_name='followers')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['user', 'review'], name='unique_review_follow')]


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


class PostSeries(models.Model):
    id = models.CharField(max_length=36, primary_key=True)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_series')
    title = models.CharField(max_length=120)
    description = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.title

    class Meta:
        ordering = ['-created_at']
        verbose_name_plural = 'Post series'


class PostSeriesItem(models.Model):
    series = models.ForeignKey(PostSeries, on_delete=models.CASCADE, related_name='items')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='series_items')
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ['order']
        constraints = [
            models.UniqueConstraint(fields=['series', 'post'], name='unique_series_post'),
        ]


class CategoryFollow(models.Model):
    """User follows a category to prioritise it in their feed."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='category_follows')
    category = models.CharField(max_length=50)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'category'], name='unique_category_follow')]
        ordering = ['category']


class PostAppeal(models.Model):
    """Author appeals a moderation action on their post."""
    STATUS_CHOICES = [('pending', 'Pending'), ('approved', 'Approved'), ('rejected', 'Rejected')]
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='appeals')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='post_appeals')
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    moderator_note = models.TextField(blank=True, default='')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']


class PostCoAuthor(models.Model):
    """Another user invited to co-author a draft post."""
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='co_authors')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='co_authored_posts')
    invited_by = models.ForeignKey(User, on_delete=models.CASCADE, related_name='coauthor_invites')
    accepted = models.BooleanField(null=True)  # None=pending, True=accepted, False=declined
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['post', 'user'], name='unique_coauthor')]
        ordering = ['-created_at']


class Challenge(models.Model):
    """Weekly community challenge."""
    title = models.CharField(max_length=100)
    description = models.TextField()
    category = models.CharField(max_length=50, blank=True, default='')
    created_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='created_challenges')
    starts_at = models.DateTimeField()
    ends_at = models.DateTimeField()
    is_active = models.BooleanField(default=True)
    winner = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='won_challenges')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-starts_at']

    def __str__(self):
        return self.title

    @property
    def is_ongoing(self):
        from django.utils import timezone
        return self.starts_at <= timezone.now() <= self.ends_at


class RankedChoiceVote(models.Model):
    """Stores a single rank preference in a ranked-choice poll."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='ranked_votes')
    poll = models.ForeignKey(Poll, on_delete=models.CASCADE, related_name='ranked_votes')
    option = models.ForeignKey(PollOption, on_delete=models.CASCADE, related_name='ranked_votes')
    rank = models.PositiveSmallIntegerField(help_text='1 = first choice, 2 = second, etc.')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=['user', 'poll', 'option'], name='unique_ranked_choice_vote_option'),
            models.UniqueConstraint(fields=['user', 'poll', 'rank'], name='unique_ranked_choice_vote_rank'),
        ]
        ordering = ['rank']

    def __str__(self):
        return f"{self.user.username} ranked '{self.option.text}' #{self.rank} on {self.poll.title}"


class ChallengeEntry(models.Model):
    """A post submitted as an entry to a challenge."""
    challenge = models.ForeignKey(Challenge, on_delete=models.CASCADE, related_name='entries')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='challenge_entries')
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='challenge_entries')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['challenge', 'post'], name='unique_challenge_entry')]
        ordering = ['-created_at']


class Story(models.Model):
    """Ephemeral post visible for 24 hours, inspired by Instagram Stories."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='stories')
    content = models.TextField(blank=True, default='')
    image = models.ImageField(upload_to='stories/', null=True, blank=True)
    bg_color = models.CharField(max_length=20, default='#0ea5e9', help_text='Background gradient colour for text stories')
    expires_at = models.DateTimeField(db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username}'s story ({self.id})"

    @property
    def is_expired(self):
        from django.utils import timezone
        return timezone.now() >= self.expires_at

    class Meta:
        ordering = ['-created_at']


class StoryView(models.Model):
    story = models.ForeignKey(Story, on_delete=models.CASCADE, related_name='views')
    viewer = models.ForeignKey(User, on_delete=models.CASCADE, related_name='story_views')
    viewed_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['story', 'viewer'], name='unique_story_view')]
        ordering = ['-viewed_at']


class FeedScore(models.Model):
    """Pre-computed personalised feed score for a (user, post) pair."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='feed_scores')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='feed_scores')
    score = models.FloatField(default=0.0, db_index=True)
    computed_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'post'], name='unique_feed_score')]
        ordering = ['-score']


class PostInsight(models.Model):
    """Daily analytics snapshot for a post (creator analytics)."""
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='insights')
    date = models.DateField(db_index=True)
    unique_viewers = models.PositiveIntegerField(default=0)
    total_impressions = models.PositiveIntegerField(default=0)
    likes_count = models.PositiveIntegerField(default=0)
    comments_count = models.PositiveIntegerField(default=0)
    saves_count = models.PositiveIntegerField(default=0)
    debates_count = models.PositiveIntegerField(default=0)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['post', 'date'], name='unique_post_insight_day')]
        ordering = ['-date']


class ReadLater(models.Model):
    """Post queued for reading later by a user."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='read_later')
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='read_later_by')
    is_read = models.BooleanField(default=False)
    added_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user.username} → read later: {self.post_id}"

    class Meta:
        ordering = ['-added_at']
        constraints = [models.UniqueConstraint(fields=['user', 'post'], name='unique_read_later')]


class DirectMessage(models.Model):
    """Private 1-to-1 message between two users."""
    sender = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sent_messages')
    recipient = models.ForeignKey(User, on_delete=models.CASCADE, related_name='received_messages')
    content = models.TextField(max_length=2000)
    is_read = models.BooleanField(default=False, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['created_at']
        indexes = [
            models.Index(fields=['sender', 'recipient']),
            models.Index(fields=['recipient', 'is_read']),
        ]

    def __str__(self):
        return f"DM {self.sender.username}→{self.recipient.username}: {self.content[:40]}"


class LinkPreview(models.Model):
    """Cached OG/meta preview for a URL found in a post."""
    url = models.URLField(max_length=500, unique=True)
    title = models.CharField(max_length=300, blank=True, default='')
    description = models.TextField(blank=True, default='')
    image_url = models.URLField(max_length=500, blank=True, default='')
    site_name = models.CharField(max_length=100, blank=True, default='')
    fetched_at = models.DateTimeField(auto_now=True)
    fetch_failed = models.BooleanField(default=False)

    class Meta:
        ordering = ['-fetched_at']

    def __str__(self):
        return f"Preview: {self.url[:60]}"


class DMRequest(models.Model):
    """Message request from a user to a non-follower."""
    STATUS_PENDING = 'pending'
    STATUS_ACCEPTED = 'accepted'
    STATUS_REJECTED = 'rejected'
    STATUS_CHOICES = [
        (STATUS_PENDING, 'Pending'),
        (STATUS_ACCEPTED, 'Accepted'),
        (STATUS_REJECTED, 'Rejected'),
    ]

    sender = models.ForeignKey(
        'auth.User', on_delete=models.CASCADE, related_name='sent_dm_requests'
    )
    recipient = models.ForeignKey(
        'auth.User', on_delete=models.CASCADE, related_name='received_dm_requests'
    )
    message = models.CharField(max_length=300, blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_PENDING, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['recipient', 'status'])]
        constraints = [models.UniqueConstraint(fields=['sender', 'recipient'], name='unique_dm_request')]

    def __str__(self):
        return f"DMRequest {self.sender_id} -> {self.recipient_id} [{self.status}]"


class PostReport(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Pending'),
        ('dismissed', 'Dismissed'),
        ('actioned', 'Actioned'),
    ]
    REASON_CHOICES = [
        ('spam', 'Spam or misleading'),
        ('misinformation', 'Misinformation'),
        ('harassment', 'Harassment'),
        ('hate_speech', 'Hate speech'),
        ('other', 'Other'),
    ]
    post = models.ForeignKey(Post, on_delete=models.CASCADE, related_name='reports')
    reporter = models.ForeignKey('auth.User', on_delete=models.CASCADE, related_name='post_reports_submitted')
    reason = models.CharField(max_length=30, choices=REASON_CHOICES, default='spam')
    details = models.TextField(blank=True, default='')
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['post', 'reporter'], name='unique_post_report_per_reporter')]

    def __str__(self):
        return f"PostReport #{self.id} on {self.post_id} by {self.reporter_id} ({self.status})"


# ─── Live Debate Rooms ────────────────────────────────────────────────────────

class LiveDebateRoom(models.Model):
    STATUS_OPEN    = 'open'
    STATUS_LIVE    = 'live'
    STATUS_VOTING  = 'voting'
    STATUS_CLOSED  = 'closed'
    STATUS_CHOICES = [
        (STATUS_OPEN,   'Open — waiting for debaters'),
        (STATUS_LIVE,   'Live — debate in progress'),
        (STATUS_VOTING, 'Voting — community voting on winner'),
        (STATUS_CLOSED, 'Closed'),
    ]
    SIDE_CHOICES = [('yes', 'Yes'), ('no', 'No')]

    id           = models.CharField(max_length=36, primary_key=True)
    title        = models.CharField(max_length=200, help_text='Debate topic / question')
    description  = models.TextField(blank=True, default='')
    creator      = models.ForeignKey(User, on_delete=models.CASCADE, related_name='created_debate_rooms')
    yes_debater  = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='debate_rooms_yes')
    no_debater   = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='debate_rooms_no')
    status       = models.CharField(max_length=10, choices=STATUS_CHOICES, default=STATUS_OPEN, db_index=True)
    winner_side  = models.CharField(max_length=3, choices=SIDE_CHOICES, blank=True, default='')
    duration_minutes = models.PositiveSmallIntegerField(default=10)
    started_at   = models.DateTimeField(null=True, blank=True)
    ends_at      = models.DateTimeField(null=True, blank=True)
    ended_at     = models.DateTimeField(null=True, blank=True)
    created_at   = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"LiveRoom: {self.title[:60]} [{self.status}]"

    @property
    def is_full(self):
        return bool(self.yes_debater_id and self.no_debater_id)

    @property
    def yes_votes(self):
        return self.live_votes.filter(winner_side='yes').count()

    @property
    def no_votes(self):
        return self.live_votes.filter(winner_side='no').count()


class LiveDebateMessage(models.Model):
    room       = models.ForeignKey(LiveDebateRoom, on_delete=models.CASCADE, related_name='messages')
    sender     = models.ForeignKey(User, on_delete=models.CASCADE, related_name='live_debate_messages')
    content    = models.TextField(max_length=1000)
    is_system  = models.BooleanField(default=False, help_text='System/event messages')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

    def __str__(self):
        return f"{self.sender.username}: {self.content[:40]}"


class LiveDebateVote(models.Model):
    SIDE_CHOICES = [('yes', 'Yes'), ('no', 'No')]
    room        = models.ForeignKey(LiveDebateRoom, on_delete=models.CASCADE, related_name='live_votes')
    voter       = models.ForeignKey(User, on_delete=models.CASCADE, related_name='live_debate_votes')
    winner_side = models.CharField(max_length=3, choices=SIDE_CHOICES)
    created_at  = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        constraints = [models.UniqueConstraint(fields=['room', 'voter'], name='unique_live_debate_vote')]

    def __str__(self):
        return f"{self.voter.username} voted {self.winner_side} wins room {self.room_id}"
