# DiscussionHub Django Backend - Installation Guide

## Complete Setup Instructions

### Step 1: MySQL Database Setup

First, create the MySQL database. Use MySQL Command Line or MySQL Workbench:

```sql
CREATE DATABASE discussionhub_db;
CREATE USER 'root'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON discussionhub_db.* TO 'root'@'localhost';
FLUSH PRIVILEGES;
```

### Step 2: Project Location

Your Django project is already set up at:
```
d:\DescussionHub\discussio-hub-django\
```

### Step 3: Activate Virtual Environment

```bash
cd d:\DescussionHub\discussio-hub-django
venv\Scripts\activate
```

You should see `(venv)` prefix in your terminal.

### Step 4: Configure .env File

Edit: `d:\DescussionHub\discussio-hub-django\.env`

```env
DEBUG=True
SECRET_KEY=your-secret-key-change-this-in-production
ALLOWED_HOSTS=localhost,127.0.0.1

DB_NAME=discussionhub_db
DB_USER=root
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=3306

CORS_ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
```

Replace:
- `your_password` - Your MySQL root password
- `SECRET_KEY` - Generate a secure key (run: `python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"`)

### Step 5: Install Dependencies

```bash
pip install -r requirements.txt
```

### Step 6: Run Migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

### Step 7: Create Superuser (Admin Account)

```bash
python manage.py createsuperuser
```

Follow the prompts to create an admin account:
- Username: admin
- Email: admin@example.com
- Password: (secure password)

### Step 8: Run Development Server

```bash
python manage.py runserver
```

The backend will be available at:
- **API**: http://127.0.0.1:8000/api/
- **Admin Panel**: http://127.0.0.1:8000/admin/

## Project Structure

```
discussio-hub-django/
├── config/                      # Django project settings
│   ├── settings.py             # Configuration file
│   ├── urls.py                 # Main URLs
│   ├── wsgi.py                 # WSGI configuration
│   └── __init__.py
├── users/                       # User & Profile app
│   ├── models.py               # User Profile model
│   ├── views.py                # User API views
│   ├── serializers.py          # User serializers
│   ├── admin.py                # Admin configuration
│   ├── apps.py
│   ├── signals.py              # Auto-create profile on user registration
│   └── migrations/
├── discussions/                # Posts, Comments & Debates app
│   ├── models.py               # Post, Comment, Debate models
│   ├── views.py                # Discussion API views
│   ├── serializers.py          # Discussion serializers
│   ├── admin.py                # Admin configuration
│   ├── apps.py
│   └── migrations/
├── api/                         # API routing
│   ├── urls.py                 # API endpoints
│   ├── apps.py
│   └── __init__.py
├── manage.py                    # Django management script
├── requirements.txt             # Python dependencies
├── .env                         # Environment variables
└── README.md                    # Documentation
```

## Database Models

### Profile
- user (ForeignKey to User)
- username (unique)
- avatar_url (optional)
- created_at
- updated_at

### Post
- id (UUID)
- user (ForeignKey)
- title
- content  
- category (choice)
- created_at
- updated_at

### Comment
- id (UUID)
- post (ForeignKey)
- user (ForeignKey)
- content
- vote_type (yes/no)
- likes (count)
- dislikes (count)
- created_at
- updated_at

### Debate
- id (UUID)
- comment (ForeignKey)
- post (ForeignKey)
- initiator (ForeignKey to User)
- target (ForeignKey to User)
- status (pending/accepted/rejected/completed)
- created_at
- updated_at

## Common Commands

```bash
# Create superuser
python manage.py createsuperuser

# Run migrations
python manage.py makemigrations
python manage.py migrate

# Run development server
python manage.py runserver

# Run tests
python manage.py test

# Create app (for future apps)
python manage.py startapp appname

# Shell (interactive Django shell)
python manage.py shell

# View all URLs
python manage.py show_urls
```

## Troubleshooting

### Port 8000 Already in Use
```bash
python manage.py runserver 8001
```

### MySQL Connection Error
1. Check MySQL is running
2. Verify credentials in .env
3. Ensure database exists
4. Test connection: `mysql -u root -p`

### Module Not Found
```bash
pip install -r requirements.txt
```

### Permission Denied on Linux/Mac
```bash
chmod +x manage.py
```

## Connect Frontend to Backend

In your React/Vite frontend `.env`:

```env
VITE_API_URL=http://localhost:8000/api
```

Update API calls to use the Django backend instead of Supabase:

```javascript
// Before (Supabase)
const { data } = await supabase.from('posts').select('*')

// After (Django REST API)
const response = await fetch('http://localhost:8000/api/posts/')
const data = await response.json()
```

## API Authentication

Use Token Authentication:

1. **Login** to get token:
```bash
curl -X POST http://localhost:8000/api/users/login/ \
  -H "Content-Type: application/json" \
  -d '{"username":"admin", "password":"password"}'
```

2. **Use token** in requests:
```bash
curl -H "Authorization: Token YOUR_TOKEN" \
  http://localhost:8000/api/posts/
```

## Admin Panel Access

1. Go to: http://127.0.0.1:8000/admin/
2. Login with superuser credentials
3. Manage all data (Users, Posts, Comments, Debates)

## Production Deployment

For production:
1. Change `DEBUG=False` in .env
2. Set proper SECRET_KEY
3. Use environment-specific settings
4. Configure allowed hosts properly
5. Use Gunicorn + Nginx
6. Set up SSL/HTTPS
7. Use environment variables for secrets

See (Django Deployment Checklist)[https://docs.djangoproject.com/en/4.2/howto/deployment/checklist/]

## Next Steps

1. Install dependencies
2. Configure MySQL & .env
3. Run migrations
4. Create superuser
5. Run development server
6. Update React frontend to use Django API
7. Test API endpoints

Good luck with your DiscussionHub Django backend! 🚀
