from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Comment, Notification, PostFollow

@receiver(post_save, sender=Comment)
def notify_post_followers(sender, instance, created, **kwargs):
    if not created:
        return

    post = instance.post
    followers = PostFollow.objects.filter(post=post).exclude(user=instance.user).select_related('user')

    for follow in followers:
        # Count comments made since this user followed the post
        comments_since_follow = Comment.objects.filter(
            post=post,
            created_at__gte=follow.created_at
        ).count()

        # Notify for every 5 comments since the user followed
        if comments_since_follow > 0 and comments_since_follow % 5 == 0:
            Notification.objects.create(
                user=follow.user,
                post=post,
                notification_type='post_activity',
                message=f'{comments_since_follow} new comments on "{post.title}" since you followed it.'
            )
