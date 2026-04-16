from users.models import Profile
prasad_profiles = Profile.objects.filter(username='prasad12')
print('Profiles with prasad12:', prasad_profiles.count())
for p in prasad_profiles:
    print(p.user.username if p.user else 'No user')