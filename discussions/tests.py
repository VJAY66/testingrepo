import uuid

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient

from discussions.models import Post


class DailyPostLimitTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='poster', password='secret123')
        self.category = 'Technology'

    def create_posts(self, total):
        for index in range(total):
            Post.objects.create(
                id=str(uuid.uuid4()),
                user=self.user,
                title=f'Post {index}',
                content='content',
                category=self.category,
            )

    def test_frontend_create_post_blocks_daily_limit(self):
        self.create_posts(50)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse('create_post'),
            {
                'title': 'Blocked post',
                'content': 'content',
                'category': self.category,
            },
            follow=True,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Post.objects.filter(user=self.user).count(), 50)
        messages = list(response.context['messages'])
        self.assertTrue(any('50 posts per day' in str(message) for message in messages))

    def test_api_create_post_blocks_daily_limit(self):
        self.create_posts(50)
        client = APIClient()
        client.force_authenticate(user=self.user)

        response = client.post(
            reverse('post-list'),
            {
                'title': 'Blocked post',
                'content': 'content',
                'category': self.category,
            },
            format='json',
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(Post.objects.filter(user=self.user).count(), 50)
        self.assertEqual(response.data['detail'], 'You can create up to 50 posts per day.')