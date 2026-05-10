from django.urls import path
from . import views

urlpatterns = [
    path('', views.index, name='index'),
    path('suggested/', views.suggested, name='suggested'),
    path('category/<str:category_name>/', views.category, name='category'),
    path('discussion/<str:post_id>/', views.discussion, name='discussion'),
    path('profile/', views.profile, name='profile'),
    path('profile/upload-picture/', views.upload_profile_picture, name='upload_profile_picture'),
    path('profile/who-viewed/', views.who_viewed_profile, name='who_viewed_profile'),
    path('user/<str:username>/', views.user_profile, name='user_profile'),
    path('user/<str:username>/report/', views.report_user_profile, name='report_user_profile'),
    path('search/', views.search, name='search'),
    path('search/quick/', views.quick_search, name='quick_search'),
    path('search/users/', views.search_users, name='search_users'),
    path('search/hashtags/suggest/', views.hashtag_suggestions, name='hashtag_suggestions'),
    path('search/hashtags/', views.hashtag_search, name='hashtag_search'),
    path('ask/', views.ask_question, name='ask_question'),
    path('notifications/', views.notifications, name='notifications'),
    path('notifications/inbox/', views.notification_center, name='notification_center'),
    path('notifications/inbox/<int:notif_id>/read/', views.mark_notification_read, name='mark_notification_read'),
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
    path('onboarding/follow/', views.onboarding_step2, name='onboarding_step2'),
    path('onboarding/profile/', views.onboarding_step3, name='onboarding_step3'),
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
    path('counter-debate/<str:debate_id>/', views.counter_debate, name='counter_debate'),
    path('accept-counter-debate/<str:debate_id>/', views.accept_counter_debate, name='accept_counter_debate'),
    path('debates/<str:debate_id>/chat/', views.debate_chat, name='debate_chat'),
    path('debates/<str:debate_id>/messages/', views.send_debate_message, name='send_debate_message'),
    path('debates/<str:debate_id>/messages/<int:message_id>/update/', views.update_debate_message, name='update_debate_message'),
    path('debates/<str:debate_id>/messages/<int:message_id>/react/', views.react_to_debate_message, name='react_to_debate_message'),
    path('debates/<str:debate_id>/messages/<int:message_id>/report/', views.report_debate_message, name='report_debate_message'),
    path('debates/<str:debate_id>/messages/list/', views.debate_messages, name='debate_messages'),
    path('debates/<str:debate_id>/messages/mark-read/', views.mark_debate_read, name='mark_debate_read'),
    path('moderation/debate-message-reports/<int:report_id>/review/', views.moderate_debate_message_report, name='moderate_debate_message_report'),
    path('moderation/profile-reports/<int:report_id>/review/', views.moderate_profile_report, name='moderate_profile_report'),
    path('moderation/post-reports/<int:report_id>/review/', views.moderate_post_report, name='moderate_post_report'),
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
    path('polls/<str:poll_id>/predict/', views.predict_poll, name='predict_poll'),
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
    path('reviews/<str:review_id>/pin-comment/', views.pin_review_comment, name='pin_review_comment'),
    # Trending hashtags
    path('trending/', views.trending_hashtags, name='trending_hashtags'),
    # Activity feed
    path('activity/', views.activity_feed, name='activity_feed'),
    # Debate transcript (public)
    path('debates/<str:debate_id>/transcript/', views.debate_transcript, name='debate_transcript'),
    path('debates/<str:debate_id>/recap/', views.debate_recap, name='debate_recap'),
    # Observer voting
    path('debates/<str:debate_id>/observer-vote/', views.observer_vote, name='observer_vote'),
    # User blocking
    path('block-user/', views.block_user, name='block_user'),
    path('unblock-user/', views.unblock_user, name='unblock_user'),
    # User muting
    path('mute-user/', views.mute_user, name='mute_user'),
    path('unmute-user/', views.unmute_user, name='unmute_user'),
    path('profile/muted/', views.muted_users_list, name='muted_users'),
    # Follow requests (private profiles)
    path('follow-requests/', views.follow_requests_list, name='follow_requests_list'),
    path('follow-requests/<int:req_id>/approve/', views.approve_follow_request, name='approve_follow_request'),
    path('follow-requests/<int:req_id>/deny/', views.deny_follow_request, name='deny_follow_request'),
    path('profile/privacy/', views.toggle_profile_privacy, name='toggle_profile_privacy'),
    # Profile bio/website update
    path('profile/update-bio/', views.update_profile_bio, name='update_profile_bio'),
    path('profile/theme/', views.save_theme_preference, name='save_theme_preference'),
    # Notification preferences
    path('profile/notification-prefs/', views.update_notification_prefs, name='update_notification_prefs'),
    path('profile/quiet-hours/', views.update_quiet_hours, name='update_quiet_hours'),
    path('profile/mention-setting/', views.update_mention_setting, name='update_mention_setting'),
    # Comment reporting
    path('report-post-comment/', views.report_post_comment, name='report_post_comment'),
    # Debate stats JSON
    path('stats/debates/', views.debate_stats, name='debate_stats'),
    path('stats/debates/<str:username>/', views.debate_stats, name='debate_stats_user'),
    # Hashtag following
    path('hashtags/follow/', views.follow_hashtag, name='follow_hashtag'),
    path('hashtags/feed/', views.hashtag_followed_feed, name='hashtag_followed_feed'),
    path('scheduled/', views.scheduled_posts, name='scheduled_posts'),
    # Moderation dashboard
    path('moderation/', views.moderation_dashboard, name='moderation_dashboard'),
    # Draft posts & pinned posts
    path('posts/<str:post_id>/publish/', views.publish_draft, name='publish_draft'),
    path('posts/<str:post_id>/reschedule/', views.reschedule_draft, name='reschedule_draft'),
    path('posts/<str:post_id>/reminder/', views.set_post_reminder, name='set_post_reminder'),
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
    path('following/', views.following_feed, name='following_feed'),
    # Explore / Discover
    path('explore/', views.explore, name='explore'),
    # Stories
    path('stories/', views.stories_list, name='stories_list'),
    path('stories/create/', views.create_story, name='create_story'),
    path('stories/<int:story_id>/', views.view_story, name='view_story'),
    path('stories/<int:story_id>/delete/', views.delete_story, name='delete_story'),
    # Creator Analytics
    path('analytics/', views.analytics_dashboard, name='analytics_dashboard'),
    path('posts/<str:post_id>/analytics/', views.creator_analytics, name='creator_analytics'),
    path('posts/<str:post_id>/analytics/refresh/', views.refresh_post_insight, name='refresh_post_insight'),
    # Close Friends
    path('close-friends/', views.close_friends_list_view, name='close_friends_list'),
    path('close-friends/toggle/', views.toggle_close_friend, name='toggle_close_friend'),
    # People You May Know
    path('people-you-may-know/', views.people_you_may_know, name='people_you_may_know'),
    # Trending / Rising Creators
    path('trending-users/', views.trending_users, name='trending_users'),
    # Post audience
    path('posts/<str:post_id>/audience/', views.update_post_audience, name='update_post_audience'),
    # Profile highlights
    path('profile/highlights/toggle/', views.toggle_highlight, name='toggle_highlight'),
    # Debate round timer
    path('debates/<str:debate_id>/timer/', views.debate_timer_status, name='debate_timer_status'),
    path('debates/<str:debate_id>/timer/set/', views.debate_set_timer, name='debate_set_timer'),
    # Mutual draw
    path('debates/<str:debate_id>/propose-draw/', views.debate_propose_draw, name='debate_propose_draw'),
    # Read Later
    path('read-later/', views.read_later_list, name='read_later'),
    path('read-later/toggle/', views.toggle_read_later, name='toggle_read_later'),
    path('read-later/mark-done/', views.mark_read_later_done, name='mark_read_later_done'),
    # Reaction insights (discussion page)
    path('posts/<str:post_id>/reaction-insights/', views.post_reaction_insights, name='post_reaction_insights'),
    # Account management
    path('account/delete/', views.account_delete, name='account_delete'),
    path('account/delete/cancel/', views.account_delete_cancel, name='account_delete_cancel'),
    path('account/password/', views.password_change, name='password_change'),
    # Private DMs — fixed paths must come before the parametric dm/<str:username>/
    path('dm/', views.dm_list, name='dm_list'),
    path('dm/delete/', views.dm_delete, name='dm_delete'),
    path('dm/unread/', views.dm_unread_count, name='dm_unread_count'),
    path('dm/requests/', views.dm_requests_list, name='dm_requests_list'),
    path('dm/request/<str:username>/', views.dm_request_send, name='dm_request_send'),
    path('dm/request/<int:request_id>/respond/', views.dm_request_respond, name='dm_request_respond'),
    path('dm/<str:username>/', views.dm_thread, name='dm_thread'),
    path('dm/<str:username>/poll/', views.dm_thread_poll, name='dm_thread_poll'),
    # Push notifications
    path('push/subscribe/', views.push_subscribe, name='push_subscribe'),
    path('push/unsubscribe/', views.push_unsubscribe, name='push_unsubscribe'),
    path('push/vapid-key/', views.push_vapid_public_key, name='push_vapid_key'),
    # Link preview
    path('link-preview/', views.link_preview, name='link_preview'),
    # Trending categories
    path('trending-categories/', views.trending_categories, name='trending_categories'),
    # Cookie consent
    path('cookie-consent/', views.cookie_consent, name='cookie_consent'),
    # Privacy policy
    path('privacy/', views.privacy_policy, name='privacy_policy'),
    # Ban / Unban
    path('user/<str:username>/ban/', views.ban_user, name='ban_user'),
    path('user/<str:username>/unban/', views.unban_user, name='unban_user'),
    # Post embed
    path('posts/<str:post_id>/embed/', views.post_embed, name='post_embed'),
    # Advanced search
    path('search/advanced/', views.search_advanced, name='search_advanced'),
    # Related posts API
    path('posts/<str:post_id>/related/', views.related_posts_api, name='related_posts_api'),
    # SSE v2 (notifications + DM count)
    path('notifications/stream/v2/', views.notification_stream_v2, name='notification_stream_v2'),
    # Blocked users management
    path('profile/blocked/', views.blocked_users_list, name='blocked_users'),
    # Muted keywords management
    path('profile/muted-keywords/', views.muted_keywords_page, name='muted_keywords_page'),
    # Post report
    path('posts/<str:post_id>/report/', views.report_post, name='report_post'),
    # Notification preferences page (GET)
    path('profile/notification-prefs/page/', views.notification_prefs_page, name='notification_prefs_page'),
    # Pinned comments
    path('comments/<str:comment_id>/pin/', views.pin_comment, name='pin_comment'),
    path('comments/<str:comment_id>/unpin/', views.unpin_comment, name='unpin_comment'),
    # Trending debates
    path('trending/', views.trending_debates, name='trending_debates'),
    # What you missed
    path('what-you-missed/', views.what_you_missed, name='what_you_missed'),
    # Share post to DM
    path('posts/<str:post_id>/share-dm/', views.share_post_to_dm, name='share_post_to_dm'),
    # User activity heatmap
    path('profile/<str:username>/heatmap/', views.user_activity_heatmap, name='user_activity_heatmap'),
    # Bulk notification management
    path('notifications/bulk/', views.bulk_notifications, name='bulk_notifications'),
    # Export my data
    path('account/export/', views.export_my_data, name='export_my_data'),
    # Two-factor authentication
    path('account/2fa/', views.totp_setup, name='totp_setup'),
    path('account/2fa/verify-setup/', views.totp_verify_setup, name='totp_verify_setup'),
    path('account/2fa/disable/', views.totp_disable, name='totp_disable'),
    path('account/2fa/login/', views.totp_login_verify, name='totp_login_verify'),
    # Post similarity check
    path('posts/similarity-check/', views.post_similarity_check, name='post_similarity_check'),
    # Debate Hall of Fame
    path('debates/hall-of-fame/', views.debate_hall_of_fame, name='debate_hall_of_fame'),
    # Live Debate Rooms
    path('live-debates/', views.live_debate_rooms, name='live_debate_rooms'),
    path('live-debates/create/', views.create_live_debate_room, name='create_live_debate_room'),
    path('live-debates/<str:room_id>/', views.live_debate_room_detail, name='live_debate_room_detail'),
    path('live-debates/<str:room_id>/join/', views.join_live_debate_room, name='join_live_debate_room'),
    path('live-debates/<str:room_id>/messages/', views.live_debate_poll_messages, name='live_debate_poll_messages'),
    path('live-debates/<str:room_id>/send/', views.live_debate_send_message, name='live_debate_send_message'),
    path('live-debates/<str:room_id>/vote/', views.live_debate_vote, name='live_debate_vote'),
    # Bookmarks
    path('bookmarks/', views.bookmarks, name='bookmarks'),
    path('posts/<str:post_id>/bookmark/', views.toggle_bookmark, name='toggle_bookmark'),
]