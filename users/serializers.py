from rest_framework import serializers
from django.contrib.auth.models import User
from users.models import Profile

class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ['id', 'username', 'avatar_url', 'created_at']
        read_only_fields = ['created_at']

class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)
    
    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'profile', 'date_joined']
        read_only_fields = ['id', 'date_joined']

class UserRegistrationSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, min_length=8)
    username = serializers.CharField(max_length=30)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password']

    def validate_password(self, value):
        import re
        if not re.search(r'[A-Z]', value):
            raise serializers.ValidationError("Password must contain an uppercase letter")
        if not re.search(r'[a-z]', value):
            raise serializers.ValidationError("Password must contain a lowercase letter")
        if not re.search(r'[0-9]', value):
            raise serializers.ValidationError("Password must contain a number")
        if not re.search(r'[!@#$%^&*(),.?":{}|<>]', value):
            raise serializers.ValidationError("Password must contain a special character")
        return value

    def create(self, validated_data):
        username = validated_data.pop('username')
        user = User.objects.create_user(
            username=username,
            email=validated_data['email'],
            password=validated_data['password']
        )
        Profile.objects.create(user=user, username=username)
        return user
