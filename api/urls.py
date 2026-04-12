from django.urls import path, include
from rest_framework.routers import DefaultRouter
from users.views import UserViewSet, ProfileViewSet
from discussions.views import PostViewSet, CommentViewSet, DebateViewSet

router = DefaultRouter()
router.register(r'users', UserViewSet, basename='user')
router.register(r'profiles', ProfileViewSet, basename='profile')
router.register(r'posts', PostViewSet, basename='post')
router.register(r'comments', CommentViewSet, basename='comment')
router.register(r'debates', DebateViewSet, basename='debate')

urlpatterns = [
    path('', include(router.urls)),
    path('api-auth/', include('rest_framework.urls')),
]
