from rest_framework import serializers
from discussions.models import Post, Comment, Debate
from users.serializers import ProfileSerializer

class CommentSerializer(serializers.ModelSerializer):
    profiles = ProfileSerializer(source='user.profile', read_only=True)
    
    class Meta:
        model = Comment
        fields = ['id', 'post', 'user', 'content', 'vote_type', 'likes', 'dislikes', 'profiles', 'created_at']
        read_only_fields = ['id', 'created_at']

class PostSerializer(serializers.ModelSerializer):
    profiles = ProfileSerializer(source='user.profile', read_only=True)
    comments = CommentSerializer(many=True, read_only=True)
    comment_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Post
        fields = ['id', 'user', 'title', 'content', 'category', 'hashtags', 'created_at', 'profiles', 'comments', 'comment_count']
        read_only_fields = ['id', 'user', 'created_at']

    def get_comment_count(self, obj):
        return obj.comments.count()

class DebateSerializer(serializers.ModelSerializer):
    initiator_username = serializers.CharField(source='initiator.username', read_only=True)
    target_username = serializers.CharField(source='target.username', read_only=True)
    
    class Meta:
        model = Debate
        fields = ['id', 'comment', 'post', 'initiator', 'target', 'initiator_username', 'target_username', 'status', 'created_at']
        read_only_fields = ['id', 'created_at']
