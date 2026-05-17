from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.sitemaps.views import sitemap
from django.views.generic import TemplateView, RedirectView
from frontend.sitemaps import PostSitemap, StaticSitemap, CategorySitemap
from frontend.feeds import LatestPostsFeed, CategoryFeed, UserPostFeed, UserPostAtomFeed

sitemaps = {
    'posts': PostSitemap,
    'static': StaticSitemap,
    'categories': CategorySitemap,
}

urlpatterns = [
    path('favicon.ico', RedirectView.as_view(url='/static/favicon.ico', permanent=True)),
    path('admin/', admin.site.urls),
    path('api/', include('api.urls')),
    path('', include('frontend.urls')),
    # Sitemap & robots
    path('sitemap.xml', sitemap, {'sitemaps': sitemaps}, name='django.contrib.sitemaps.views.sitemap'),
    path('robots.txt', TemplateView.as_view(template_name='robots.txt', content_type='text/plain')),
    # RSS feeds
    path('feed/', LatestPostsFeed(), name='feed_latest'),
    path('feed/<str:category_name>/', CategoryFeed(), name='feed_category'),
    path('feed/user/<str:username>/', UserPostFeed(), name='feed_user'),
    path('feed/user/<str:username>/atom/', UserPostAtomFeed(), name='feed_user_atom'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
