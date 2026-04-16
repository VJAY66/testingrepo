from users.models import Profile
profiles = Profile.objects.filter(user_id=5)
print('Profiles with user_id=5:', profiles.count())
for p in profiles:
    print('Profile id:', p.id, 'username:', p.username, 'user:', p.user.username if p.user else 'None')