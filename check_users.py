from django.contrib.auth.models import User
users = User.objects.filter(username='prasad12')
print('Users with prasad12:', users.count())
for u in users:
    print(u.username, u.id)