import uuid

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from discussions.models import Comment, Debate, DebateParticipant, Post
from frontend.views import get_frontend_categories


class FrontendCategoryTests(TestCase):
	def test_history_category_is_available_in_frontend_categories(self):
		categories = get_frontend_categories()
		category_names = [category['name'] for category in categories]

		self.assertIn('History', category_names)
		self.assertLess(category_names.index('History'), category_names.index('Others'))

		history_category = next(category for category in categories if category['name'] == 'History')
		self.assertEqual(history_category['icon'], '🏺')


class DebateVisibilityTests(TestCase):
	def setUp(self):
		self.post_owner = User.objects.create_user(username='owner', password='pass12345')
		self.target_user = User.objects.create_user(username='target', password='pass12345')
		self.initiator_user = User.objects.create_user(username='initiator', password='pass12345')
		self.viewer_user = User.objects.create_user(username='viewer', password='pass12345')

		self.post = Post.objects.create(
			id=str(uuid.uuid4()),
			user=self.post_owner,
			title='Debate topic',
			content='Topic content',
			category='Technology',
		)

		self.target_comment = Comment.objects.create(
			id=str(uuid.uuid4()),
			post=self.post,
			user=self.target_user,
			vote_type='no',
			content='Target no opinion',
		)

		self.viewer_comment = Comment.objects.create(
			id=str(uuid.uuid4()),
			post=self.post,
			user=self.viewer_user,
			vote_type='yes',
			content='Viewer yes opinion',
		)

		self.accepted_debate = Debate.objects.create(
			id=str(uuid.uuid4()),
			comment=self.target_comment,
			post=self.post,
			initiator=self.initiator_user,
			target=self.target_user,
			status='accepted',
			yes_supporters=1,
			no_supporters=1,
		)

		DebateParticipant.objects.create(
			debate=self.accepted_debate,
			user=self.initiator_user,
			side='yes',
			is_active=True,
		)
		DebateParticipant.objects.create(
			debate=self.accepted_debate,
			user=self.target_user,
			side='no',
			is_active=True,
		)

	def test_start_debate_returns_view_redirect_when_side_is_full(self):
		self.client.force_login(self.viewer_user)

		response = self.client.post(
			reverse('start_debate'),
			{'comment_id': self.target_comment.id},
		)

		self.assertEqual(response.status_code, 200)
		data = response.json()
		self.assertTrue(data['success'])
		self.assertTrue(data['queued'])
		self.assertIn('/debates/', data['redirect_url'])
		self.assertIn('/chat/', data['redirect_url'])

		participation = DebateParticipant.objects.get(debate=self.accepted_debate, user=self.viewer_user)
		self.assertEqual(participation.side, 'yes')
		self.assertFalse(participation.is_active)

	def test_discussion_shows_view_debate_when_side_is_full(self):
		self.client.force_login(self.viewer_user)

		response = self.client.get(reverse('discussion', kwargs={'post_id': self.post.id}))

		self.assertEqual(response.status_code, 200)
		self.assertContains(response, 'View Debate')
		self.assertContains(response, f'/debates/{self.accepted_debate.id}/chat/')
		self.assertNotContains(response, 'Join Debate')


class DebateLimitUpdateTests(TestCase):
	def setUp(self):
		self.post_owner = User.objects.create_user(username='post_owner', password='pass12345')
		self.host_user = User.objects.create_user(username='host_user', password='pass12345')
		self.initiator_user = User.objects.create_user(username='initiator_user', password='pass12345')

		self.post = Post.objects.create(
			id=str(uuid.uuid4()),
			user=self.post_owner,
			title='Debate limit topic',
			content='Topic content',
			category='Technology',
		)

		self.host_comment = Comment.objects.create(
			id=str(uuid.uuid4()),
			post=self.post,
			user=self.host_user,
			vote_type='no',
			content='Host opinion',
		)

		self.debate = Debate.objects.create(
			id=str(uuid.uuid4()),
			comment=self.host_comment,
			post=self.post,
			initiator=self.initiator_user,
			target=self.host_user,
			status='accepted',
			yes_supporters=2,
			no_supporters=2,
		)

	def test_host_can_increase_limits_during_conversation(self):
		self.client.force_login(self.host_user)

		response = self.client.post(
			reverse('increase_debate_limits', kwargs={'debate_id': self.debate.id}),
			{'yes_supporters': '4', 'no_supporters': '5'},
		)

		self.assertEqual(response.status_code, 200)
		data = response.json()
		self.assertTrue(data['success'])
		self.assertEqual(data['yes_supporters'], 4)
		self.assertEqual(data['no_supporters'], 5)

		self.debate.refresh_from_db()
		self.assertEqual(self.debate.yes_supporters, 4)
		self.assertEqual(self.debate.no_supporters, 5)

	def test_non_host_cannot_increase_limits(self):
		self.client.force_login(self.initiator_user)

		response = self.client.post(
			reverse('increase_debate_limits', kwargs={'debate_id': self.debate.id}),
			{'yes_supporters': '3', 'no_supporters': '3'},
		)

		self.assertEqual(response.status_code, 403)
		data = response.json()
		self.assertFalse(data['success'])

		self.debate.refresh_from_db()
		self.assertEqual(self.debate.yes_supporters, 2)
		self.assertEqual(self.debate.no_supporters, 2)
