from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('category/<str:category_name>/', views.category, name='category'),
    path('discussion/<int:post_id>/', views.discussion, name='discussion'),
    path('profile/', views.profile, name='profile'),
    path('search/', views.search, name='search'),
    path('notifications/', views.notifications, name='notifications'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('logout/', views.logout_view, name='logout'),
    path('create-post/', views.create_post, name='create_post'),
    path('discussion/<int:post_id>/comment/', views.create_comment, name='create_comment'),
    path('like-comment/', views.like_comment, name='like_comment'),
    path('start-debate/', views.start_debate, name='start_debate'),
    path('accept-debate/<int:debate_id>/', views.accept_debate, name='accept_debate'),
    path('reject-debate/<int:debate_id>/', views.reject_debate, name='reject_debate'),
]