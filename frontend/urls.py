from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('suggested/', views.suggested, name='suggested'),
    path('category/<str:category_name>/', views.category, name='category'),
    path('discussion/<str:post_id>/', views.discussion, name='discussion'),
    path('profile/', views.profile, name='profile'),
    path('profile/upload-picture/', views.upload_profile_picture, name='upload_profile_picture'),
    path('user/<str:username>/', views.user_profile, name='user_profile'),
    path('user/<str:username>/report/', views.report_user_profile, name='report_user_profile'),
    path('search/', views.search, name='search'),
    path('search/quick/', views.quick_search, name='quick_search'),
    path('search/users/', views.search_users, name='search_users'),
    path('search/hashtags/suggest/', views.hashtag_suggestions, name='hashtag_suggestions'),
    path('search/hashtags/', views.hashtag_search, name='hashtag_search'),
    path('ask/', views.ask_question, name='ask_question'),
    path('notifications/', views.notifications, name='notifications'),
    path('notifications/count/', views.notification_count, name='notification_count'),
    path('notifications/stream/', views.notification_stream, name='notification_stream'),
    path('notifications/mark-all-read/', views.mark_all_notifications_read, name='mark_all_notifications_read'),
    path('notifications/unfollow-post/', views.unfollow_post, name='unfollow_post'),
    path('notifications/dismiss/', views.dismiss_notification, name='dismiss_notification'),
    path('chats/', views.chats, name='chats'),
    path('chats/list/', views.chat_list, name='chat_list'),
    path('login/', views.login_view, name='login'),
    path('register/', views.register_view, name='register'),
    path('check-username/', views.check_username, name='check_username'),
    path('logout/', views.logout_view, name='logout'),
    path('interests/', views.interests_onboarding, name='interests_onboarding'),
    path('interests/save/', views.save_interests, name='save_interests'),
    path('presence/offline/', views.mark_offline, name='mark_offline'),
    path('create-post/', views.create_post, name='create_post'),
    path('profile/remove-follower/', views.remove_follower, name='remove_follower'),
    path('posts/<str:post_id>/action/', views.post_action, name='post_action'),
    path('posts/<str:post_id>/likes/', views.post_likes, name='post_likes'),
    path('posts/<str:post_id>/update/', views.update_post, name='update_post'),
    path('posts/<str:post_id>/delete/', views.delete_post, name='delete_post'),
    path('posts/<str:post_id>/remove-repost/', views.remove_repost, name='remove_repost'),
    path('discussion/<str:post_id>/comment/', views.create_comment, name='create_comment'),
    path('like-comment/', views.like_comment, name='like_comment'),
    path('start-debate/', views.start_debate, name='start_debate'),
    path('report-comment/', views.report_comment, name='report_comment'),
    path('update-comment/', views.update_comment, name='update_comment'),
    path('accept-debate/<str:debate_id>/', views.accept_debate, name='accept_debate'),
    path('reject-debate/<str:debate_id>/', views.reject_debate, name='reject_debate'),
    path('debates/<str:debate_id>/chat/', views.debate_chat, name='debate_chat'),
    path('debates/<str:debate_id>/messages/', views.send_debate_message, name='send_debate_message'),
    path('debates/<str:debate_id>/messages/<int:message_id>/update/', views.update_debate_message, name='update_debate_message'),
    path('debates/<str:debate_id>/messages/<int:message_id>/react/', views.react_to_debate_message, name='react_to_debate_message'),
    path('debates/<str:debate_id>/messages/<int:message_id>/report/', views.report_debate_message, name='report_debate_message'),
    path('debates/<str:debate_id>/messages/list/', views.debate_messages, name='debate_messages'),
    path('debates/<str:debate_id>/messages/mark-read/', views.mark_debate_read, name='mark_debate_read'),
    path('moderation/debate-message-reports/<int:report_id>/review/', views.moderate_debate_message_report, name='moderate_debate_message_report'),
    path('moderation/profile-reports/<int:report_id>/review/', views.moderate_profile_report, name='moderate_profile_report'),
    path('debates/<str:debate_id>/info/', views.debate_info, name='debate_info'),
    path('debates/<str:debate_id>/status/', views.debate_status, name='debate_status'),
    path('debates/<str:debate_id>/leave/', views.leave_debate, name='leave_debate'),
    path('debates/<str:debate_id>/rejoin/', views.rejoin_debate, name='rejoin_debate'),
    path('debates/<str:debate_id>/increase-limits/', views.increase_debate_limits, name='increase_debate_limits'),
    path('debates/<str:debate_id>/end/', views.end_debate, name='end_debate'),
    path('debates/<str:debate_id>/remove-participant/', views.remove_debate_participant, name='remove_debate_participant'),
    # Polls
    path('polls/<str:poll_id>/action/', views.poll_action, name='poll_action'),
    path('polls/', views.polls_list, name='polls_list'),
    path('polls/create/', views.create_poll, name='create_poll'),
    path('polls/comment/react/', views.like_poll_comment, name='like_poll_comment'),
    path('polls/<str:poll_id>/', views.poll_detail, name='poll_detail'),
    path('polls/<str:poll_id>/vote/', views.poll_vote, name='poll_vote'),
    path('polls/<str:poll_id>/ranked-vote/', views.poll_ranked_vote, name='poll_ranked_vote'),
    path('polls/<str:poll_id>/comment/', views.create_poll_comment, name='create_poll_comment'),
    # Questions
    path('questions/<str:question_id>/action/', views.question_action, name='question_action'),
    path('questions/', views.questions_list, name='questions_list'),
    path('questions/ask/', views.ask_general_question, name='ask_general_question'),
    path('questions/answer/<str:answer_id>/vote/', views.vote_answer, name='vote_answer'),
    path('questions/answer/<str:answer_id>/best/', views.mark_best_answer, name='mark_best_answer'),
    path('questions/<str:question_id>/', views.question_detail, name='question_detail'),
    path('questions/<str:question_id>/answer/', views.post_answer, name='post_answer'),
    # Leaderboard
    path('leaderboard/', views.leaderboard, name='leaderboard'),
    # Reviews
    path('reviews/<str:review_id>/action/', views.review_action, name='review_action'),
    path('reviews/', views.reviews_list, name='reviews_list'),
    path('reviews/create/', views.create_review, name='create_review'),
    path('reviews/comment/react/', views.like_review_comment, name='like_review_comment'),
    path('reviews/<str:review_id>/', views.review_detail, name='review_detail'),
    path('reviews/<str:review_id>/react/', views.react_to_review, name='react_to_review'),
    path('reviews/<str:review_id>/comment/', views.create_review_comment, name='create_review_comment'),
    # Trending hashtags
    path('trending/', views.trending_hashtags, name='trending_hashtags'),
    # Activity feed
    path('activity/', views.activity_feed, name='activity_feed'),
    # Debate transcript (public)
    path('debates/<str:debate_id>/transcript/', views.debate_transcript, name='debate_transcript'),
    # Observer voting
    path('debates/<str:debate_id>/observer-vote/', views.observer_vote, name='observer_vote'),
    # User blocking
    path('block-user/', views.block_user, name='block_user'),
    path('unblock-user/', views.unblock_user, name='unblock_user'),
    # Profile bio/website update
    path('profile/update-bio/', views.update_profile_bio, name='update_profile_bio'),
    path('profile/theme/', views.save_theme_preference, name='save_theme_preference'),
    # Notification preferences
    path('profile/notification-prefs/', views.update_notification_prefs, name='update_notification_prefs'),
    # Comment reporting
    path('report-post-comment/', views.report_post_comment, name='report_post_comment'),
    # Debate stats JSON
    path('stats/debates/', views.debate_stats, name='debate_stats'),
    path('stats/debates/<str:username>/', views.debate_stats, name='debate_stats_user'),
    # Hashtag following
    path('hashtags/follow/', views.follow_hashtag, name='follow_hashtag'),
    path('hashtags/feed/', views.hashtag_followed_feed, name='hashtag_followed_feed'),
    # Moderation dashboard
    path('moderation/', views.moderation_dashboard, name='moderation_dashboard'),
    # Draft posts & pinned posts
    path('posts/<str:post_id>/publish/', views.publish_draft, name='publish_draft'),
    path('posts/<str:post_id>/pin/', views.pin_post, name='pin_post'),
    # Save collections
    path('collections/create/', views.create_collection, name='create_collection'),
    path('collections/add/', views.add_to_collection, name='add_to_collection'),
    path('collections/tag-item/', views.tag_collection_item, name='tag_collection_item'),
    path('collections/remove/', views.remove_from_collection, name='remove_from_collection'),
    path('collections/<int:collection_id>/', views.collection_detail, name='collection_detail'),
    path('collections/<int:collection_id>/delete/', views.delete_collection, name='delete_collection'),
    # User verification
    path('user/<str:username>/verify/', views.toggle_verify_user, name='toggle_verify_user'),
    # Muted keywords
    path('keywords/mute/', views.add_muted_keyword, name='add_muted_keyword'),
    path('keywords/unmute/', views.remove_muted_keyword, name='remove_muted_keyword'),
    # Post series
    path('series/create/', views.create_series, name='create_series'),
    path('series/<str:series_id>/', views.series_detail, name='series_detail'),
    path('series/<str:series_id>/add/', views.add_post_to_series, name='add_post_to_series'),
    path('series/<str:series_id>/remove/', views.remove_from_series, name='remove_from_series'),
    path('posts/<str:post_id>/reaction-users/', views.post_reaction_users, name='post_reaction_users'),
    path('mentions/suggest/', views.mention_suggestions, name='mention_suggestions'),
    path('debates/inbox/', views.debate_inbox, name='debate_inbox'),
    # New feature endpoints
    path('user/<str:username>/endorse/', views.endorse_user, name='endorse_user'),
    path('categories/follow/', views.follow_category, name='follow_category'),
    path('posts/<str:post_id>/set-expiry/', views.set_post_expiry, name='set_post_expiry'),
    path('posts/<str:post_id>/appeal/', views.appeal_post, name='appeal_post'),
    path('posts/<str:post_id>/coauthor/invite/', views.invite_coauthor, name='invite_coauthor'),
    path('posts/<str:post_id>/coauthor/respond/', views.respond_coauthor_invite, name='respond_coauthor_invite'),
    path('debates/<str:debate_id>/rematch/', views.request_rematch, name='request_rematch'),
    path('appeals/<int:appeal_id>/review/', views.review_appeal, name='review_appeal'),
    path('challenges/', views.challenges_list, name='challenges_list'),
    path('challenges/create/', views.create_challenge, name='create_challenge'),
    path('challenges/<int:challenge_id>/enter/', views.enter_challenge, name='enter_challenge'),
    # For You feed (personalised algorithm)
    path('for-you/', views.for_you_feed, name='for_you_feed'),
    # Explore / Discover
    path('explore/', views.explore, name='explore'),
    # Stories
    path('stories/', views.stories_list, name='stories_list'),
    path('stories/create/', views.create_story, name='create_story'),
    path('stories/<int:story_id>/', views.view_story, name='view_story'),
    path('stories/<int:story_id>/delete/', views.delete_story, name='delete_story'),
    # Creator Analytics
    path('posts/<str:post_id>/analytics/', views.creator_analytics, name='creator_analytics'),
    path('posts/<str:post_id>/analytics/refresh/', views.refresh_post_insight, name='refresh_post_insight'),
    # Close Friends
    path('close-friends/', views.close_friends_list_view, name='close_friends_list'),
    path('close-friends/toggle/', views.toggle_close_friend, name='toggle_close_friend'),
    # People You May Know
    path('people-you-may-know/', views.people_you_may_know, name='people_you_may_know'),
    # Post audience
    path('posts/<str:post_id>/audience/', views.update_post_audience, name='update_post_audience'),
    # Ban / Unban
    path('user/<str:username>/ban/', views.ban_user, name='ban_user'),
    path('user/<str:username>/unban/', views.unban_user, name='unban_user'),
    # Post embed
    path('posts/<str:post_id>/embed/', views.post_embed, name='post_embed'),
    # DM Requests
    path('dm/requests/', views.dm_requests_list, name='dm_requests_list'),
    path('dm/request/<str:username>/', views.dm_request_send, name='dm_request_send'),
    path('dm/request/<int:request_id>/respond/', views.dm_request_respond, name='dm_request_respond'),
    # Advanced search
    path('search/advanced/', views.search_advanced, name='search_advanced'),
    # Related posts API
    path('posts/<str:post_id>/related/', views.related_posts_api, name='related_posts_api'),
    # SSE v2 (notifications + DM count)
    path('notifications/stream/v2/', views.notification_stream_v2, name='notification_stream_v2'),
]