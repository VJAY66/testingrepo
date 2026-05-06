from django.contrib.syndication.views import Feed
from django.utils.feedgenerator import Atom1Feed
from django.shortcuts import get_object_or_404
from django.contrib.auth.models import User
from django.urls import reverse
from discussions.models import Post, CATEGORY_CHOICES


class LatestPostsFeed(Feed):
    title = 'PickASide — Latest Discussions'
    link = '/'
    description = 'The latest debates and discussions on PickASide.'

    def items(self):
        return Post.objects.filter(
            is_draft=False, is_deleted_by_moderation=False
        ).select_related('user').order_by('-created_at')[:30]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return (item.content or '')[:300]

    def item_author_name(self, item):
        return item.user.username

    def item_pubdate(self, item):
        return item.created_at

    def item_link(self, item):
        return f'/discussion/{item.id}/'


class CategoryFeed(Feed):
    description = 'Latest discussions in this category on PickASide.'

    def get_object(self, request, category_name):
        return category_name

    def title(self, obj):
        return f'PickASide — {obj}'

    def link(self, obj):
        return f'/category/{obj}/'

    def items(self, obj):
        return Post.objects.filter(
            category=obj, is_draft=False, is_deleted_by_moderation=False
        ).select_related('user').order_by('-created_at')[:20]

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return (item.content or '')[:300]

    def item_author_name(self, item):
        return item.user.username

    def item_pubdate(self, item):
        return item.created_at

    def item_link(self, item):
        return f'/discussion/{item.id}/'


class UserPostFeed(Feed):
    """RSS feed of posts by a specific author."""

    def get_object(self, request, username):
        return get_object_or_404(User, username=username)

    def title(self, user):
        return f"@{user.username}'s posts — PickASide"

    def link(self, user):
        return f'/user/{user.username}/'

    def description(self, user):
        return f"Latest posts by @{user.username} on PickASide"

    def items(self, user):
        return (
            Post.objects.filter(user=user, is_draft=False, is_deleted_by_moderation=False)
            .select_related('user')
            .order_by('-created_at')[:20]
        )

    def item_title(self, item):
        return item.title

    def item_description(self, item):
        return (item.content or '')[:400]

    def item_author_name(self, item):
        return item.user.username

    def item_pubdate(self, item):
        return item.created_at

    def item_link(self, item):
        return f'/discussion/{item.id}/'


class UserPostAtomFeed(UserPostFeed):
    feed_type = Atom1Feed
    subtitle = UserPostFeed.description
