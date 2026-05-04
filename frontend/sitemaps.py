from django.contrib.sitemaps import Sitemap
from django.urls import reverse
from discussions.models import Post, CATEGORY_CHOICES


class PostSitemap(Sitemap):
    changefreq = 'weekly'
    priority = 0.8

    def items(self):
        return Post.objects.filter(is_draft=False, is_deleted_by_moderation=False).order_by('-created_at')[:5000]

    def lastmod(self, obj):
        return obj.updated_at

    def location(self, obj):
        return f'/discussion/{obj.id}/'


class StaticSitemap(Sitemap):
    changefreq = 'monthly'
    priority = 0.5

    def items(self):
        return ['index', 'polls_list', 'questions_list', 'reviews_list', 'leaderboard', 'trending_hashtags', 'explore']

    def location(self, item):
        return reverse(item)


class CategorySitemap(Sitemap):
    changefreq = 'daily'
    priority = 0.6

    def items(self):
        return [c[0] for c in CATEGORY_CHOICES]

    def location(self, item):
        return f'/category/{item}/'
