from django.contrib.syndication.views import Feed
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
