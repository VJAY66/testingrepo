from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from discussions.models import Post, PostFollow

@api_view(['POST'])
@permission_classes([IsAuthenticated])
def follow_post(request):
    post_id = request.data.get('post_id')
    action = request.data.get('action')  # 'follow' or 'unfollow'

    try:
        post = Post.objects.get(id=post_id)
    except Post.DoesNotExist:
        return Response({'success': False, 'message': 'Post not found'}, status=404)

    if action == 'follow':
        PostFollow.objects.get_or_create(user=request.user, post=post)
        message = f'Now following post "{post.title}"'
    elif action == 'unfollow':
        PostFollow.objects.filter(user=request.user, post=post).delete()
        message = f'Unfollowed post "{post.title}"'
    else:
        return Response({'success': False, 'message': 'Invalid action'})

    return Response({'success': True, 'message': message})