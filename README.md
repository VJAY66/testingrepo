# DiscussionHub Django Backend

A Django REST API backend for DiscussionHub, a platform for debate and discussion on various topics.

## Project Structure

```
discussio-hub-django/
├── config/              # Django settings & URLs
├── users/              # User & Profile app
├── discussions/        # Posts, Comments, Debates app
├── manage.py
├── requirements.txt
├── .env
└── README.md
```

## Prerequisites

- Python 3.8+
- MySQL 5.7+
- pip

## Setup Instructions

### 1. Create MySQL Database

```sql
CREATE DATABASE discussionhub_db;
CREATE USER 'root'@'localhost' IDENTIFIED BY 'your_password';
GRANT ALL PRIVILEGES ON discussionhub_db.* TO 'root'@'localhost';
FLUSH PRIVILEGES;
```

### 2. Create Virtual Environment

```bash
cd d:\DescussionHub\discussio-hub-django
python -m venv venv
venv\Scripts\activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Edit `.env` file:

```env
DEBUG=True
SECRET_KEY=your-secret-key-here
ALLOWED_HOSTS=localhost,127.0.0.1

DB_NAME=discussionhub_db
DB_USER=root
DB_PASSWORD=your_password
DB_HOST=localhost
DB_PORT=3306

CORS_ALLOWED_ORIGINS=http://localhost:5173,http://localhost:3000
```

### 5. Apply Migrations

```bash
python manage.py makemigrations
python manage.py migrate
```

### 6. Create Superuser (Admin)

```bash
python manage.py createsuperuser
```

### 7. Run Development Server

```bash
python manage.py runserver
```

The API will be available at `http://127.0.0.1:8000/`

## API Endpoints

### Authentication & Users

- **POST** `/api/users/register/` - Register new user
- **POST** `/api/users/login/` - Login user
- **POST** `/api/users/logout/` - Logout user
- **GET** `/api/users/me/` - Get current user profile
- **GET** `/api/users/{id}/` - Get user by ID
- **GET** `/api/profiles/my_profile/` - Get my profile

### Posts

- **GET** `/api/posts/` - List all posts
- **POST** `/api/posts/` - Create new post (auth required)
- **GET** `/api/posts/{id}/` - Get post details
- **PUT** `/api/posts/{id}/` - Update post (auth required)
- **DELETE** `/api/posts/{id}/` - Delete post (auth required)
- **GET** `/api/posts/by_category/?category=Technology` - Get posts by category
- **GET** `/api/posts/trending/` - Get trending posts
- **GET** `/api/posts/my_posts/` - Get my posts (auth required)
- **GET** `/api/posts/search/?q=query` - Search posts

### Comments

- **GET** `/api/comments/` - List all comments
- **POST** `/api/comments/` - Create new comment (auth required)
- **GET** `/api/comments/{id}/` - Get comment details
- **PUT** `/api/comments/{id}/` - Update comment (auth required)
- **DELETE** `/api/comments/{id}/` - Delete comment (auth required)
- **GET** `/api/comments/by_post/?post_id=id` - Get comments for specific post
- **POST** `/api/comments/like/` - Like a comment (auth required)
- **POST** `/api/comments/dislike/` - Dislike a comment (auth required)

### Debates

- **GET** `/api/debates/` - List all debates (auth required)
- **GET** `/api/debates/my_debates/` - Get my debates (auth required)
- **POST** `/api/debates/start_debate/` - Start a debate (auth required)
- **POST** `/api/debates/{id}/accept/` - Accept debate request (auth required)
- **POST** `/api/debates/{id}/reject/` - Reject debate request (auth required)

## Admin Panel

Access the Django admin panel at: `http://127.0.0.1:8000/admin/`

Login with your superuser credentials to manage:
- Users
- Profiles
- Posts
- Comments
- Debates

## Database Schema

### Users
- Custom User model from Django auth

### Profiles
- username (unique)
- avatar_url
- created_at
- updated_at

### Posts
- id (UUID)
- user (ForeignKey)
- title
- content
- category (choice field)
- created_at
- updated_at

### Comments
- id (UUID)
- post (ForeignKey)
- user (ForeignKey)
- content
- vote_type (yes/no)
- likes
- dislikes
- created_at
- updated_at

### Debates
- id (UUID)
- comment (ForeignKey)
- post (ForeignKey)
- initiator (ForeignKey to User)
- target (ForeignKey to User)
- status (pending/accepted/rejected/completed)
- created_at
- updated_at

## Categories

- Technology
- Sports
- Science
- Politics
- Entertainment
- Health
- Business
- Education
- Travel
- Food

## Authentication

The API uses Token Authentication. To authenticate:

1. Get a token by logging in:
```bash
POST /api/users/login/
{
  "username": "your_username",
  "password": "your_password"
}
```

2. Include the token in the Authorization header:
```
Authorization: Token <your_token>
```

## Frontend Integration (React/Vite)

Update your frontend `.env` to connect to the backend:

```env
VITE_API_URL=http://localhost:8000/api
```

## Troubleshooting

### MySQL Connection Error
- Ensure MySQL is running
- Check DB credentials in `.env`
- Verify database exists

### Migration Errors
```bash
python manage.py makemigrations --empty users
python manage.py migrate
```

### Port Already in Use
```bash
python manage.py runserver 8001
```

## Development

Run tests:
```bash
python manage.py test
```

Run linting:
```bash
pip install flake8
flake8
```

## Production Deployment

See Django deployment documentation for production setup with:
- Gunicorn
- Nginx
- Environment variables
- Security settings
