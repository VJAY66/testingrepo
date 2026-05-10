import os
import socket
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {'1', 'true', 'yes', 'on'}


DEBUG = _env_bool('DEBUG', default=False)

SECRET_KEY = os.getenv('SECRET_KEY', '').strip()
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = 'django-insecure-dev-only-change-me'
    else:
        raise ValueError('SECRET_KEY must be set when DEBUG is False')

def _split_env_list(name, default=''):
    return [
        value.strip().strip('"').strip("'")
        for value in os.getenv(name, default).split(',')
        if value.strip().strip('"').strip("'")
    ]


ALLOWED_HOSTS = _split_env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1')

if DEBUG:
    local_hosts = {'localhost', '127.0.0.1', '0.0.0.0', '[::1]'}

    try:
        hostname = socket.gethostname()
        if hostname:
            local_hosts.add(hostname)
            local_hosts.add(f'{hostname}.local')

        for host_info in socket.getaddrinfo(hostname, None):
            host = host_info[4][0]
            if host:
                local_hosts.add(host)
    except socket.gaierror:
        pass

    ALLOWED_HOSTS = list(dict.fromkeys([*ALLOWED_HOSTS, *sorted(local_hosts)]))

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'rest_framework',
    'rest_framework.authtoken',
    'corsheaders',
    'users',
    'discussions',
    'frontend',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'users.middleware.UpdateLastSeenMiddleware',
    'users.middleware.BanMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': False,
        'OPTIONS': {
            'loaders': [
                'django.template.loaders.filesystem.Loader',
                'django.template.loaders.app_directories.Loader',
            ],
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'frontend.context_processors.notification_counts',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

# Database Configuration
import dj_database_url

DATABASES = {
    'default': dj_database_url.config(
        default=os.getenv('DATABASE_URL', 'sqlite:///db.sqlite3')
    )
}

_cache_backend = os.getenv('CACHE_BACKEND', 'django.core.cache.backends.locmem.LocMemCache')
CACHES = {
    'default': {
        'BACKEND': _cache_backend,
        'LOCATION': os.getenv('CACHE_LOCATION', 'pickside-default'),
        'TIMEOUT': int(os.getenv('CACHE_TIMEOUT', '300')),
        **({'OPTIONS': {'CLIENT_CLASS': 'django_redis.client.DefaultClient'}} if 'redis' in _cache_backend else {}),
    }
}

# Celery
CELERY_BROKER_URL = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_RESULT_BACKEND = os.getenv('REDIS_URL', 'redis://localhost:6379/0')
CELERY_TASK_SERIALIZER = 'json'
CELERY_ACCEPT_CONTENT = ['json']
CELERY_TIMEZONE = os.getenv('TIME_ZONE', 'UTC')
CELERY_TASK_ALWAYS_EAGER = _env_bool('CELERY_TASK_ALWAYS_EAGER', default=True)  # runs synchronously unless Redis available
CELERY_BEAT_SCHEDULER = 'django_celery_beat.schedulers:DatabaseScheduler'

from celery.schedules import crontab
CELERY_BEAT_SCHEDULE = {
    # Publish scheduled posts every minute
    'publish-scheduled-posts': {
        'task': 'frontend.tasks.publish_scheduled_posts',
        'schedule': 60.0,  # every 60 seconds
    },
    # Weekly digest every Monday at 9 AM UTC
    'send-weekly-digest': {
        'task': 'frontend.tasks.send_weekly_digest',
        'schedule': crontab(hour=9, minute=0, day_of_week=1),
    },
    # Hard-delete accounts requested for deletion 30+ days ago — daily at 3 AM
    'cleanup-deleted-accounts': {
        'task': 'frontend.tasks.cleanup_deleted_accounts',
        'schedule': crontab(hour=3, minute=0),
    },
}

# Elasticsearch
ELASTICSEARCH_URL = os.getenv('ELASTICSEARCH_URL', '')  # empty = disabled, fall back to DB search
ELASTICSEARCH_INDEX_PREFIX = os.getenv('ELASTICSEARCH_INDEX_PREFIX', 'pickside')

# Push Notifications (VAPID)
VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', '')
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '')
VAPID_ADMIN_EMAIL = os.getenv('VAPID_ADMIN_EMAIL', os.getenv('DEFAULT_FROM_EMAIL', 'PickASide <noreply@pickside.app>'))

AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATICFILES_DIRS = [
    os.path.join(BASE_DIR, 'static'),
]
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework Configuration
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticatedOrReadOnly',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_THROTTLE_CLASSES': [
        'rest_framework.throttling.AnonRateThrottle',
        'rest_framework.throttling.UserRateThrottle',
    ],
    'DEFAULT_THROTTLE_RATES': {
        'anon': os.getenv('DRF_THROTTLE_ANON', '60/min'),
        'user': os.getenv('DRF_THROTTLE_USER', '300/min'),
        'login': os.getenv('DRF_THROTTLE_LOGIN', '12/min'),
    },
}

LOGIN_ATTEMPT_LIMIT = int(os.getenv('LOGIN_ATTEMPT_LIMIT', '8'))
LOGIN_ATTEMPT_WINDOW_SECONDS = int(os.getenv('LOGIN_ATTEMPT_WINDOW_SECONDS', '300'))
LOGIN_ATTEMPT_RETENTION_DAYS = int(os.getenv('LOGIN_ATTEMPT_RETENTION_DAYS', '30'))

DAILY_POST_LIMIT = int(os.getenv('DAILY_POST_LIMIT', '50'))

# OpenAI Configuration
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '').strip()
ENABLE_CONTENT_MODERATION = _env_bool('ENABLE_CONTENT_MODERATION', default=True)
ABUSIVE_MODERATION_SCORE_THRESHOLD = float(os.getenv('ABUSIVE_MODERATION_SCORE_THRESHOLD', '0.75'))
MODERATION_FAIL_CLOSED = _env_bool('MODERATION_FAIL_CLOSED', default=False)

# CORS Configuration
CORS_ALLOWED_ORIGINS = _split_env_list('CORS_ALLOWED_ORIGINS', '')
if DEBUG and not CORS_ALLOWED_ORIGINS:
    CORS_ALLOWED_ORIGINS = ['http://localhost:5173', 'http://localhost:3000']

CORS_ALLOW_CREDENTIALS = _env_bool('CORS_ALLOW_CREDENTIALS', default=True)

# Security hardening (safe defaults for production)
CSRF_TRUSTED_ORIGINS = _split_env_list('CSRF_TRUSTED_ORIGINS', '')
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', default=not DEBUG)
SESSION_COOKIE_SECURE = _env_bool('SESSION_COOKIE_SECURE', default=not DEBUG)
CSRF_COOKIE_SECURE = _env_bool('CSRF_COOKIE_SECURE', default=not DEBUG)
SESSION_COOKIE_HTTPONLY = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
SECURE_REFERRER_POLICY = 'strict-origin-when-cross-origin'

if not DEBUG:
    SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', '31536000'))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', default=True)
    SECURE_HSTS_PRELOAD = _env_bool('SECURE_HSTS_PRELOAD', default=True)
else:
    SECURE_HSTS_SECONDS = 0
    SECURE_HSTS_INCLUDE_SUBDOMAINS = False
    SECURE_HSTS_PRELOAD = False

LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'

MANUAL_EDITOR_USERNAME = os.getenv('MANUAL_EDITOR_USERNAME', '').strip()
MANUAL_EDITOR_PASSWORD = os.getenv('MANUAL_EDITOR_PASSWORD', '')
MANUAL_EDITOR_EMAIL = os.getenv('MANUAL_EDITOR_EMAIL', '').strip()

# Backend-only moderator accounts for chat abuse reports.
# Example: MODERATOR_USERNAMES=alice,bob,charlie
MODERATOR_USERNAMES = _split_env_list('MODERATOR_USERNAMES', 'seshu')

# ── Email ─────────────────────────────────────────────────────────────────────
EMAIL_BACKEND = os.getenv('EMAIL_BACKEND', 'django.core.mail.backends.console.EmailBackend')
EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
EMAIL_USE_TLS = os.getenv('EMAIL_USE_TLS', 'True') == 'True'
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', '')
EMAIL_HOST_PASSWORD = os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'PickASide <noreply@pickside.app>')
SITE_URL = os.getenv('SITE_URL', 'https://pickside.app')
