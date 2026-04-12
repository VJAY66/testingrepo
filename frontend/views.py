from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth.decorators import login_required
from django.contrib.auth import login, authenticate, logout
from django.contrib import messages
from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django.db.models import Q
import json

from discussions.models import Post, Comment, Debate
from users.models import Profile

def index(request):
    """Home page with trending posts and categories"""
    trending_posts = Post.objects.order_by('-created_at')[:10]

    categories = [
        {'name': 'Technology', 'icon': '💻', 'color': 'bg-blue-500/10 text-blue-400 border-blue-500/20'},
        {'name': 'Sports', 'icon': '⚽', 'color': 'bg-green-500/10 text-green-400 border-green-500/20'},
        {'name': 'Science', 'icon': '🔬', 'color': 'bg-purple-500/10 text-purple-400 border-purple-500/20'},
        {'name': 'Politics', 'icon': '🏛️', 'color': 'bg-red-500/10 text-red-400 border-red-500/20'},
        {'name': 'Entertainment', 'icon': '🎬', 'color': 'bg-yellow-500/10 text-yellow-400 border-yellow-500/20'},
        {'name': 'Health', 'icon': '🏥', 'color': 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20'},
        {'name': 'Business', 'icon': '📊', 'color': 'bg-indigo-500/10 text-indigo-400 border-indigo-500/20'},
        {'name': 'Education', 'icon': '📚', 'color': 'bg-orange-500/10 text-orange-400 border-orange-500/20'},
        {'name': 'Travel', 'icon': '✈️', 'color': 'bg-cyan-500/10 text-cyan-400 border-cyan-500/20'},
        {'name': 'Food', 'icon': '🍕', 'color': 'bg-pink-500/10 text-pink-400 border-pink-500/20'},
    ]

    context = {
        'trending_posts': trending_posts,
        'categories': categories,
    }
    return render(request, 'frontend/index.html', context)

def category(request, category_name):
    """Category page showing posts in a specific category"""
    posts = Post.objects.filter(category=category_name).order_by('-created_at')

    context = {
        'category_name': category_name,
        'posts': posts,
    }
    return render(request, 'frontend/category.html', context)

def discussion(request, post_id):
    """Discussion page for a specific post"""
    post = get_object_or_404(Post, id=post_id)
    comments = Comment.objects.filter(post=post).order_by('created_at')

    yes_comments = comments.filter(vote_type='yes')
    no_comments = comments.filter(vote_type='no')

    context = {
        'post': post,
        'yes_comments': yes_comments,
        'no_comments': no_comments,
    }
    return render(request, 'frontend/discussion.html', context)

@login_required
def profile(request):
    """User profile page"""
    user_posts = Post.objects.filter(user=request.user).order_by('-created_at')

    context = {
        'user_posts': user_posts,
    }
    return render(request, 'frontend/profile.html', context)

def search(request):
    """Search page"""
    query = request.GET.get('q', '')
    results = []

    if query:
        results = Post.objects.filter(
            Q(title__icontains=query) | Q(content__icontains=query)
        ).order_by('-created_at')

    context = {
        'query': query,
        'results': results,
    }
    return render(request, 'frontend/search.html', context)

@login_required
def notifications(request):
    """Notifications page showing debates"""
    debates = Debate.objects.filter(
        Q(initiator=request.user) | Q(target=request.user)
    ).order_by('-created_at')

    context = {
        'debates': debates,
    }
    return render(request, 'frontend/notifications.html', context)

def login_view(request):
    """Login page"""
    if request.method == 'POST':
        username = request.POST.get('username')
        password = request.POST.get('password')

        user = authenticate(request, username=username, password=password)
        if user is not None:
            login(request, user)
            messages.success(request, 'Welcome back!')
            return redirect('index')
        else:
            messages.error(request, 'Invalid credentials')

    return render(request, 'frontend/login.html')

def register_view(request):
    """Registration page"""
    if request.method == 'POST':
        username = request.POST.get('username')
        email = request.POST.get('email')
        password = request.POST.get('password')
        confirm_password = request.POST.get('confirm_password')

        if password != confirm_password:
            messages.error(request, 'Passwords do not match')
            return render(request, 'frontend/register.html')

        # Password validation
        if len(password) < 8:
            messages.error(request, 'Password must be at least 8 characters')
            return render(request, 'frontend/register.html')

        if not any(char.isupper() for char in password):
            messages.error(request, 'Password must contain an uppercase letter')
            return render(request, 'frontend/register.html')

        if not any(char.islower() for char in password):
            messages.error(request, 'Password must contain a lowercase letter')
            return render(request, 'frontend/register.html')

        if not any(char.isdigit() for char in password):
            messages.error(request, 'Password must contain a number')
            return render(request, 'frontend/register.html')

        if not any(char in '!@#$%^&*(),.?":{}|<>' for char in password):
            messages.error(request, 'Password must contain a special character')
            return render(request, 'frontend/register.html')

        try:
            from django.contrib.auth.models import User
            user = User.objects.create_user(
                username=username,
                email=email,
                password=password
            )
            Profile.objects.create(user=user, username=username)
            messages.success(request, 'Account created successfully! Please login.')
            return redirect('login')
        except Exception as e:
            messages.error(request, f'Registration failed: {str(e)}')

    return render(request, 'frontend/register.html')

def logout_view(request):
    """Logout view"""
    logout(request)
    messages.success(request, 'Logged out successfully')
    return redirect('index')

@login_required
@require_POST
def create_post(request):
    """Create a new post"""
    if request.method == 'POST':
        title = request.POST.get('title', '').strip()
        content = request.POST.get('content', '').strip()
        category = request.POST.get('category', '').strip()

        if not title or not category:
            messages.error(request, 'Title and category are required')
            return redirect('index')

        post = Post.objects.create(
            user=request.user,
            title=title,
            content=content,
            category=category
        )

        messages.success(request, 'Post created successfully!')
        return redirect('discussion', post_id=post.id)

    return redirect('index')

@login_required
@require_POST
def create_comment(request, post_id):
    """Create a comment on a post"""
    post = get_object_or_404(Post, id=post_id)

    if request.method == 'POST':
        vote_type = request.POST.get('vote_type')
        content = request.POST.get('content', '').strip()

        if not vote_type or vote_type not in ['yes', 'no']:
            messages.error(request, 'Invalid vote type')
            return redirect('discussion', post_id=post_id)

        Comment.objects.create(
            post=post,
            user=request.user,
            vote_type=vote_type,
            content=content if content else ''
        )

        messages.success(request, 'Comment added!')
        return redirect('discussion', post_id=post_id)

    return redirect('discussion', post_id=post_id)

@login_required
@require_POST
def like_comment(request):
    """Like or dislike a comment"""
    comment_id = request.POST.get('comment_id')
    action = request.POST.get('action')  # 'like' or 'dislike'

    try:
        comment = Comment.objects.get(id=comment_id)
        if action == 'like':
            comment.likes += 1
        elif action == 'dislike':
            comment.dislikes += 1
        comment.save()

        return JsonResponse({'success': True})
    except Comment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Comment not found'})

@login_required
@require_POST
def start_debate(request):
    """Start a debate with another user"""
    comment_id = request.POST.get('comment_id')

    try:
        comment = Comment.objects.get(id=comment_id)
        target_user = comment.user

        if request.user == target_user:
            return JsonResponse({'success': False, 'error': 'Cannot debate with yourself'})

        # Check if debate already exists
        existing_debate = Debate.objects.filter(
            comment=comment,
            initiator=request.user,
            target=target_user
        ).exists()

        if existing_debate:
            return JsonResponse({'success': False, 'error': 'Debate request already sent'})

        Debate.objects.create(
            comment=comment,
            post=comment.post,
            initiator=request.user,
            target=target_user,
            status='pending'
        )

        return JsonResponse({'success': True, 'message': 'Debate request sent!'})
    except Comment.DoesNotExist:
        return JsonResponse({'success': False, 'error': 'Comment not found'})

@login_required
@require_POST
def accept_debate(request, debate_id):
    """Accept a debate request"""
    try:
        debate = Debate.objects.get(id=debate_id, target=request.user)
        debate.status = 'accepted'
        debate.save()
        messages.success(request, 'Debate accepted!')
    except Debate.DoesNotExist:
        messages.error(request, 'Debate not found')

    return redirect('notifications')

@login_required
@require_POST
def reject_debate(request, debate_id):
    """Reject a debate request"""
    try:
        debate = Debate.objects.get(id=debate_id, target=request.user)
        debate.status = 'rejected'
        debate.save()
        messages.success(request, 'Debate rejected!')
    except Debate.DoesNotExist:
        messages.error(request, 'Debate not found')

    return redirect('notifications')
