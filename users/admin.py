from django.contrib import admin
from users.models import Profile

@admin.register(Profile)
class ProfileAdmin(admin.ModelAdmin):
    list_display = ['username', 'user', 'created_at']
    search_fields = ['username', 'user__email']
    readonly_fields = ['created_at', 'updated_at']
