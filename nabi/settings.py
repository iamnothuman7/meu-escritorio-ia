import os
import secrets
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('NABI_DATA_DIR', str(Path(os.environ.get('LOCALAPPDATA', Path.home() / '.local/share')) / 'NabiOffice'))).resolve()
DATA_DIR.mkdir(parents=True, exist_ok=True)
secret_file = DATA_DIR / 'session.key'
try:
    with secret_file.open('x', encoding='utf-8') as stream:
        stream.write(secrets.token_urlsafe(64))
    secret_file.chmod(0o600)
except FileExistsError:
    pass
SECRET_KEY = secret_file.read_text(encoding='utf-8').strip()
DEBUG = False
ALLOWED_HOSTS = [host.strip() for host in os.environ.get('NABI_ALLOWED_HOSTS', '127.0.0.1,localhost,[::1]').split(',') if host.strip()]
if 'test' in sys.argv:
    ALLOWED_HOSTS.append('testserver')
HTTPS_ENABLED = os.environ.get('NABI_HTTPS', '0') == '1'
CSRF_TRUSTED_ORIGINS = [origin.strip() for origin in os.environ.get('NABI_CSRF_TRUSTED_ORIGINS', '').split(',') if origin.strip()]
SECURE_SSL_REDIRECT = HTTPS_ENABLED
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https') if HTTPS_ENABLED else None
ROOT_URLCONF = 'nabi.urls'
WSGI_APPLICATION = 'nabi.wsgi.application'
INSTALLED_APPS = ['django.contrib.auth', 'django.contrib.contenttypes', 'django.contrib.sessions', 'django.contrib.messages', 'django.contrib.staticfiles', 'office']
MIDDLEWARE = ['office.middleware.TrustedProxyMiddleware', 'django.middleware.security.SecurityMiddleware', 'django.contrib.sessions.middleware.SessionMiddleware', 'django.middleware.common.CommonMiddleware', 'django.middleware.csrf.CsrfViewMiddleware', 'django.contrib.auth.middleware.AuthenticationMiddleware', 'django.contrib.messages.middleware.MessageMiddleware', 'django.middleware.clickjacking.XFrameOptionsMiddleware', 'office.middleware.LocalSecurityMiddleware']
TEMPLATES = [{'BACKEND':'django.template.backends.django.DjangoTemplates','DIRS':[BASE_DIR / 'office/templates'],'APP_DIRS':True,'OPTIONS':{'context_processors':['django.template.context_processors.request','django.contrib.auth.context_processors.auth','django.contrib.messages.context_processors.messages']}}]
DATABASES = {'default': {'ENGINE':'django.db.backends.sqlite3','NAME':DATA_DIR / 'office.sqlite3','OPTIONS':{'timeout':20}}}
AUTH_PASSWORD_VALIDATORS = [{'NAME':'django.contrib.auth.password_validation.MinimumLengthValidator','OPTIONS':{'min_length':10}},{'NAME':'django.contrib.auth.password_validation.CommonPasswordValidator'},{'NAME':'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'}]
LANGUAGE_CODE = 'pt-br'
TIME_ZONE = 'America/Fortaleza'
USE_I18N = True
USE_TZ = True
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / '.static'
MEDIA_ROOT = DATA_DIR / 'media'
MEDIA_ROOT.mkdir(exist_ok=True)
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
LOGIN_URL = '/login/'
LOGIN_REDIRECT_URL = '/'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SECURE = HTTPS_ENABLED
SECURE_HSTS_SECONDS = 15552000 if HTTPS_ENABLED else 0
CSRF_COOKIE_SECURE = HTTPS_ENABLED
SESSION_COOKIE_SAMESITE = 'Strict'
CSRF_COOKIE_SAMESITE = 'Strict'
SESSION_EXPIRE_AT_BROWSER_CLOSE = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'
DATA_UPLOAD_MAX_MEMORY_SIZE = 5 * 1024 * 1024
FILE_UPLOAD_MAX_MEMORY_SIZE = 2 * 1024 * 1024
CACHES = {'default': {'BACKEND': 'django.core.cache.backends.locmem.LocMemCache', 'LOCATION': 'nabi-office-security', 'OPTIONS': {'MAX_ENTRIES': 10000}}}
AUTH_POST_LIMIT = 10
AUTH_POST_WINDOW_SECONDS = 300
LOGGING = {'version':1,'disable_existing_loggers':False,'handlers':{'console':{'class':'logging.StreamHandler'}},'loggers':{'django.request':{'handlers':['console'],'level':'WARNING','propagate':False}}}
