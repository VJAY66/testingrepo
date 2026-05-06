from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAuthenticated
from rest_framework.authtoken.models import Token
from django.contrib.auth.models import User
from django.utils import timezone
from users.models import Profile, Follow, FollowRequest
from users.security import is_login_rate_limited, record_login_attempt
from users.throttles import LoginRateThrottle
from users.serializers import UserSerializer, UserRegistrationSerializer, ProfileSerializer

class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer

    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def register(self, request):
        serializer = UserRegistrationSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            return Response(UserSerializer(user).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], permission_classes=[IsAuthenticated])
    def me(self, request):
        user = request.user
        serializer = UserSerializer(user)
        return Response(serializer.data)

    @action(detail=False, methods=['post'], permission_classes=[AllowAny], throttle_classes=[LoginRateThrottle])
    def login(self, request):
        from django.contrib.auth import authenticate
        username = (request.data.get('username') or '').strip()
        password = request.data.get('password')

        if is_login_rate_limited(request, username, source='api'):
            return Response({'error': 'Too many login attempts. Please try again shortly.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
        
        user = authenticate(username=username, password=password)
        if user:
            record_login_attempt(request, username, successful=True, source='api')
            Profile.objects.update_or_create(
                user=user,
                defaults={
                    'username': user.username,
                    'last_seen': timezone.now(),
                },
            )
            Token.objects.filter(user=user).delete()
            token = Token.objects.create(user=user)
            return Response({
                'user': UserSerializer(user).data,
                'token': token.key
            })
        record_login_attempt(request, username, successful=False, source='api')
        return Response({'error': 'Invalid credentials'}, status=status.HTTP_401_UNAUTHORIZED)

    @action(detail=False, methods=['post'], permission_classes=[IsAuthenticated])
    def follow(self, request):
        username = request.data.get('username')
        action = request.data.get('action')  # 'follow' or 'unfollow'

        try:
            target_user = User.objects.get(username=username)
        except User.DoesNotExist:
            return Response({'success': False, 'message': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

        if target_user == request.user:
            return Response({'success': False, 'message': 'Cannot follow yourself'}, status=status.HTTP_400_BAD_REQUEST)

        target_profile = Profile.objects.filter(user=target_user).first()
        is_private = bool(target_profile and target_profile.is_private)

        if action == 'follow':
            if is_private:
                # Already following (approved)?
                already_follows = Follow.objects.filter(follower=request.user, following=target_user).exists()
                if already_follows:
                    return Response({
                        'success': True,
                        'message': f'Already following {username}',
                        'is_following': True,
                        'request_pending': False,
                        'followers_count': target_user.follower_links.count(),
                        'following_count': target_user.following_links.count(),
                        'my_following_count': request.user.following_links.count(),
                    })
                req, created = FollowRequest.objects.get_or_create(
                    from_user=request.user, to_user=target_user,
                    defaults={'status': FollowRequest.STATUS_PENDING},
                )
                if not created and req.status == FollowRequest.STATUS_DENIED:
                    req.status = FollowRequest.STATUS_PENDING
                    req.save(update_fields=['status', 'updated_at'])
                return Response({
                    'success': True,
                    'message': f'Follow request sent to {username}',
                    'is_following': False,
                    'request_pending': True,
                    'followers_count': target_user.follower_links.count(),
                    'following_count': target_user.following_links.count(),
                    'my_following_count': request.user.following_links.count(),
                })
            else:
                Follow.objects.get_or_create(follower=request.user, following=target_user)
                message = f'Now following {username}'
                is_following = True
        elif action == 'unfollow':
            Follow.objects.filter(follower=request.user, following=target_user).delete()
            FollowRequest.objects.filter(from_user=request.user, to_user=target_user).delete()
            message = f'Unfollowed {username}'
            is_following = False
        else:
            return Response({'success': False, 'message': 'Invalid action'}, status=status.HTTP_400_BAD_REQUEST)

        return Response({
            'success': True,
            'message': message,
            'is_following': is_following,
            'request_pending': False,
            'followers_count': target_user.follower_links.count(),
            'following_count': target_user.following_links.count(),
            'my_following_count': request.user.following_links.count(),
        })

class ProfileViewSet(viewsets.ModelViewSet):
    queryset = Profile.objects.all()
    serializer_class = ProfileSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=False, methods=['get'])
    def my_profile(self, request):
        try:
            profile = request.user.profile
            serializer = ProfileSerializer(profile)
            return Response(serializer.data)
        except Profile.DoesNotExist:
            return Response({'error': 'Profile not found'}, status=status.HTTP_404_NOT_FOUND)
