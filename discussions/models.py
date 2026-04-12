from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone

CATEGORY_CHOICES = [
    ('Technology', 'Technology'),
    ('Sports', 'Sports'),
    ('Science', 'Science'),
    ('Politics', 'Politics'),
    ('Entertainment', 'Entertainment'),
    ('Health', 'Health'),
    ('Business', 'Business'),
    ('Education', 'Education'),
    ('Travel', 'Travel'),
    ('Food', 'Food'),
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
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"Debate between {self.initiator.username} and {self.target.username}"

    class Meta:
        ordering = ['-created_at']
