from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.exceptions import ValidationError
from rest_framework.throttling import ScopedRateThrottle
from django.core.cache import cache
from django.db.models import Q
import uuid

_COMMENTS_CACHE_TTL = 30  # seconds


def _comments_cache_key(post_id):
    return f'comments_by_post:{post_id}'

from discussions.limits import has_reached_daily_post_limit
from discussions.models import Post, Comment, Debate, CommentReaction, Notification
from discussions.serializers import PostSerializer, CommentSerializer, DebateSerializer
from utils.moderation import check_content_moderation

class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects.all()
    serializer_class = PostSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'post'

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()

    def get_queryset(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return Post.objects.filter(user=self.request.user)
        return Post.objects.all()

    def perform_create(self, serializer):
        limit_reached, _, limit = has_reached_daily_post_limit(self.request.user)
        if limit_reached:
            raise ValidationError({'detail': f'You can create up to {limit} posts per day.'})

        title = serializer.validated_data.get('title', '')
        content = serializer.validated_data.get('content', '')
        combined_text = f"{title} {content}".strip()

        if combined_text and check_content_moderation(combined_text):
            raise ValidationError({'detail': 'Your post contains abusive language and cannot be posted.'})

        serializer.save(user=self.request.user, id=str(uuid.uuid4()))

    def perform_update(self, serializer):
        title = serializer.validated_data.get('title', '')
        content = serializer.validated_data.get('content', '')
        combined_text = f"{title} {content}".strip()

        if combined_text and check_content_moderation(combined_text):
            raise ValidationError({'detail': 'Your post edit contains abusive language and cannot be saved.'})

        serializer.save()

    @action(detail=False, methods=['get'])
    def by_category(self, request):
        category = request.query_params.get('category')
        if category:
            posts = Post.objects.filter(category=category).order_by('-created_at')
            serializer = PostSerializer(posts, many=True)
            return Response(serializer.data)
        return Response({'error': 'Category parameter required'}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'])
    def trending(self, request):
        posts = Post.objects.order_by('-created_at')[:10]
        serializer = PostSerializer(posts, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def my_posts(self, request):
        if not request.user.is_authenticated:
            return Response({'error': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)
        posts = Post.objects.filter(user=request.user).order_by('-created_at')
        serializer = PostSerializer(posts, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['get'])
    def search(self, request):
        query = request.query_params.get('q')
        if query:
            posts = Post.objects.filter(
                Q(title__icontains=query) | Q(content__icontains=query)
            ).order_by('-created_at')
            serializer = PostSerializer(posts, many=True)
            return Response(serializer.data)
        return Response([])

class CommentViewSet(viewsets.ModelViewSet):
    queryset = Comment.objects.all()
    serializer_class = CommentSerializer
    permission_classes = [AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'comment'

    def get_throttles(self):
        # Read-only actions don't count against the comment write throttle.
        if self.action in ('by_post', 'list', 'retrieve'):
            return []
        return super().get_throttles()

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()

    def get_queryset(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return Comment.objects.filter(user=self.request.user)
        return Comment.objects.all()

    def perform_create(self, serializer):
        post = serializer.validated_data.get('post')
        if post and Comment.objects.filter(post=post, user=self.request.user).exists():
            raise ValidationError({'detail': 'You can comment only once on a post.'})

        content = serializer.validated_data.get('content', '').strip()
        if content and check_content_moderation(content):
            raise ValidationError({'detail': 'Your comment contains abusive language and cannot be posted.'})

        serializer.save(user=self.request.user, id=str(uuid.uuid4()))
        # Bust the cache so the new comment appears immediately.
        if post:
            cache.delete(_comments_cache_key(str(post.id)))

    @action(detail=False, methods=['get'])
    def by_post(self, request):
        post_id = request.query_params.get('post_id')
        if not post_id:
            return Response({'error': 'post_id parameter required'}, status=status.HTTP_400_BAD_REQUEST)

        cache_key = _comments_cache_key(post_id)
        data = cache.get(cache_key)
        if data is None:
            comments = (
                Comment.objects
                .filter(post_id=post_id)
                .select_related('user', 'user__profile')
                .order_by('created_at')
            )
            data = CommentSerializer(comments, many=True).data
            cache.set(cache_key, data, _COMMENTS_CACHE_TTL)

        return Response(data)

    @action(detail=False, methods=['post'])
    def like(self, request):
        comment_id = request.data.get('comment_id')

        if not request.user.is_authenticated:
            return Response({'error': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            comment = Comment.objects.get(id=comment_id)

            if comment.user_id == request.user.id:
                return Response({'error': 'You cannot react to your own comment.'}, status=status.HTTP_403_FORBIDDEN)

            reaction, created = CommentReaction.objects.get_or_create(
                comment=comment,
                user=request.user,
                defaults={'reaction': 'like'}
            )

            if not created and reaction.reaction != 'like':
                reaction.reaction = 'like'
                reaction.save(update_fields=['reaction', 'updated_at'])

            likes_count = CommentReaction.objects.filter(comment=comment, reaction='like').count()
            dislikes_count = CommentReaction.objects.filter(comment=comment, reaction='dislike').count()
            comment.likes = likes_count
            comment.dislikes = dislikes_count
            comment.save(update_fields=['likes', 'dislikes', 'updated_at'])

            return Response({'likes': likes_count, 'dislikes': dislikes_count})
        except Comment.DoesNotExist:
            return Response({'error': 'Comment not found'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['post'])
    def dislike(self, request):
        comment_id = request.data.get('comment_id')

        if not request.user.is_authenticated:
            return Response({'error': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)

        try:
            comment = Comment.objects.get(id=comment_id)

            if comment.user_id == request.user.id:
                return Response({'error': 'You cannot react to your own comment.'}, status=status.HTTP_403_FORBIDDEN)

            reaction, created = CommentReaction.objects.get_or_create(
                comment=comment,
                user=request.user,
                defaults={'reaction': 'dislike'}
            )

            if not created and reaction.reaction != 'dislike':
                reaction.reaction = 'dislike'
                reaction.save(update_fields=['reaction', 'updated_at'])

            likes_count = CommentReaction.objects.filter(comment=comment, reaction='like').count()
            dislikes_count = CommentReaction.objects.filter(comment=comment, reaction='dislike').count()
            comment.likes = likes_count
            comment.dislikes = dislikes_count
            comment.save(update_fields=['likes', 'dislikes', 'updated_at'])

            return Response({'likes': likes_count, 'dislikes': dislikes_count})
        except Comment.DoesNotExist:
            return Response({'error': 'Comment not found'}, status=status.HTTP_404_NOT_FOUND)

class DebateViewSet(viewsets.ModelViewSet):
    queryset = Debate.objects.all()
    serializer_class = DebateSerializer
    permission_classes = [IsAuthenticated]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'debate'

    def get_queryset(self):
        if self.action in ['update', 'partial_update', 'destroy']:
            return Debate.objects.filter(initiator=self.request.user)
        return Debate.objects.all()

    def perform_create(self, serializer):
        serializer.save(id=str(uuid.uuid4()))

    @action(detail=False, methods=['get'])
    def my_debates(self, request):
        debates = Debate.objects.filter(
            Q(initiator=request.user) | Q(target=request.user)
        ).order_by('-created_at')
        serializer = DebateSerializer(debates, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def start_debate(self, request):
        comment_id = request.data.get('comment_id')
        try:
            comment = Comment.objects.get(id=comment_id)
            target_user = comment.user
            
            if request.user == target_user:
                return Response({'error': 'Cannot start debate with yourself'}, status=status.HTTP_400_BAD_REQUEST)

            existing_debate = Debate.objects.filter(
                comment=comment,
                initiator=request.user,
                target=target_user
            ).exists()

            if existing_debate:
                return Response({'error': 'Debate request already sent'}, status=status.HTTP_400_BAD_REQUEST)

            pending_for_target = Debate.objects.filter(target=target_user, status='pending')
            category_pending_count = pending_for_target.filter(comment__vote_type=comment.vote_type).count()
            total_pending_count = pending_for_target.count()
            side_label = 'YES' if comment.vote_type == 'yes' else 'NO'

            if category_pending_count >= 5:
                return Response(
                    {
                        'queued': True,
                        'error': f'You are in queue. {side_label} queue is full (5/5), commentor still not responding to existing requests.'
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )

            if total_pending_count >= 10:
                return Response(
                    {
                        'queued': True,
                        'error': 'You are in queue. This user already has 10 pending requests, commentor still not responding to existing requests.'
                    },
                    status=status.HTTP_429_TOO_MANY_REQUESTS
                )

            debate = Debate.objects.create(
                id=str(uuid.uuid4()),
                comment=comment,
                post=comment.post,
                initiator=request.user,
                target=target_user,
                status='pending'
            )
            return Response(DebateSerializer(debate).data, status=status.HTTP_201_CREATED)
        except Comment.DoesNotExist:
            return Response({'error': 'Comment not found'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail='pk', methods=['post'])
    def accept(self, request, pk=None):
        try:
            debate = Debate.objects.get(id=pk)
            if debate.target != request.user:
                return Response({'error': 'Only target can accept debate'}, status=status.HTTP_403_FORBIDDEN)
            debate.status = 'accepted'
            debate.save()
            return Response(DebateSerializer(debate).data)
        except Debate.DoesNotExist:
            return Response({'error': 'Debate not found'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail='pk', methods=['post'])
    def reject(self, request, pk=None):
        try:
            debate = Debate.objects.get(id=pk)
            if debate.target != request.user:
                return Response({'error': 'Only target can reject debate'}, status=status.HTTP_403_FORBIDDEN)
            debate.status = 'rejected'
            debate.save()
            return Response(DebateSerializer(debate).data)
        except Debate.DoesNotExist:
            return Response({'error': 'Debate not found'}, status=status.HTTP_404_NOT_FOUND)
