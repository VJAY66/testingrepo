from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from django.db.models import Q
import uuid

from discussions.models import Post, Comment, Debate
from discussions.serializers import PostSerializer, CommentSerializer, DebateSerializer

class PostViewSet(viewsets.ModelViewSet):
    queryset = Post.objects.all()
    serializer_class = PostSerializer
    permission_classes = [AllowAny]

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, id=str(uuid.uuid4()))

    def perform_update(self, serializer):
        serializer.save(user=self.request.user)

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

    def get_permissions(self):
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            self.permission_classes = [IsAuthenticated]
        return super().get_permissions()

    def perform_create(self, serializer):
        serializer.save(user=self.request.user, id=str(uuid.uuid4()))

    @action(detail=False, methods=['get'])
    def by_post(self, request):
        post_id = request.query_params.get('post_id')
        if post_id:
            comments = Comment.objects.filter(post_id=post_id).order_by('created_at')
            serializer = CommentSerializer(comments, many=True)
            return Response(serializer.data)
        return Response({'error': 'post_id parameter required'}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['post'])
    def like(self, request):
        comment_id = request.data.get('comment_id')
        try:
            comment = Comment.objects.get(id=comment_id)
            comment.likes += 1
            comment.save()
            return Response({'likes': comment.likes})
        except Comment.DoesNotExist:
            return Response({'error': 'Comment not found'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['post'])
    def dislike(self, request):
        comment_id = request.data.get('comment_id')
        try:
            comment = Comment.objects.get(id=comment_id)
            comment.dislikes += 1
            comment.save()
            return Response({'dislikes': comment.dislikes})
        except Comment.DoesNotExist:
            return Response({'error': 'Comment not found'}, status=status.HTTP_404_NOT_FOUND)

class DebateViewSet(viewsets.ModelViewSet):
    queryset = Debate.objects.all()
    serializer_class = DebateSerializer
    permission_classes = [IsAuthenticated]

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
