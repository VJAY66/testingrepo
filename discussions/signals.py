from django.db.models.signals import post_save
from django.dispatch import receiver
from .models import Comment, Notification, PostFollow

@receiver(post_save, sender=Comment)
def notify_post_followers(sender, instance, created, **kwargs):
    if not created:
        return

    post = instance.post
    followers = PostFollow.objects.filter(post=post).exclude(user=instance.user).select_related('user')

    # Count total comments on the post
    total_comments = Comment.objects.filter(post=post).count()

    for follow in followers:
        # Notify for every 5th comment or first comment (active conversation)
        if total_comments == 1 or total_comments % 5 == 0:
            notification_type = 'post_activity' if total_comments > 1 else 'post_comment'
            message = f'New activity on "{post.title}": {instance.user.username} commented.' if total_comments == 1 else f'Active conversation on "{post.title}": {total_comments} comments so far.'

            Notification.objects.create(
                user=follow.user,
                post=post,
                notification_type=notification_type,
                message=message
            )