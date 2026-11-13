"""
Django settings for VenomLink project.

VenomLink — Antivenom Availability and Stock Intelligence System
Designed for healthcare logistics and public-health monitoring in Nigeria.
"""

from pathlib import Path
import os
import sys

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

# Add apps directory to sys.path so apps can be imported cleanly
sys.path.insert(0, str(BASE_DIR))


def _load_env_file(path):
    """
    Minimal .env loader (no extra dependency). Real environment variables always win;
    values in the file are only used when the variable is not already set.
    Lines: KEY=VALUE, '#' comments and blank lines ignored. Values are never printed.
    """
    try:
        with open(path, encoding='utf-8') as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith('#') or '=' not in line:
                    continue
                key, _, value = line.partition('=')
                key = key.strip()
                value = value.strip().strip('"').strip("'")
                if key:
                    os.environ.setdefault(key, value)
    except FileNotFoundError:
        pass


_load_env_file(os.getenv('VENOMLINK_ENV_FILE', str(BASE_DIR / '.env')))


def _env_bool(name, default=False):
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in ('true', '1', 'yes', 'on')


def _env_list(name, default=''):
    return [item.strip() for item in os.getenv(name, default).split(',') if item.strip()]


# --------------------------------------------------------------------------
# Core security settings. SAFE BY DEFAULT: DEBUG is OFF unless explicitly enabled.
# --------------------------------------------------------------------------
DEBUG = _env_bool('DEBUG', False)

_INSECURE_DEFAULT_KEY = 'django-insecure-venomlink-dev-only-do-not-use-in-production'
SECRET_KEY = os.getenv('SECRET_KEY', '')
if not SECRET_KEY:
    if DEBUG:
        SECRET_KEY = _INSECURE_DEFAULT_KEY
    else:
        raise ImproperlyConfigured(
            "SECRET_KEY environment variable is required when DEBUG is off."
        )

if DEBUG:
    ALLOWED_HOSTS = _env_list('ALLOWED_HOSTS', 'localhost,127.0.0.1,0.0.0.0')
else:
    ALLOWED_HOSTS = _env_list('ALLOWED_HOSTS', '')
    if not ALLOWED_HOSTS:
        raise ImproperlyConfigured(
            "ALLOWED_HOSTS must be set (comma-separated hostnames) when DEBUG is off."
        )
    if SECRET_KEY.startswith('django-insecure') or len(SECRET_KEY) < 50:
        raise ImproperlyConfigured(
            "SECRET_KEY is insecure (placeholder or shorter than 50 characters). "
            "Generate one with: python -c \"from django.core.management.utils import "
            "get_random_secret_key as g; print(g())\""
        )

# Application definition
INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',

    # Third-party
    'rest_framework',
    'corsheaders',

    # VenomLink core modular apps
    'apps.facilities.apps.FacilitiesConfig',
    'apps.inventory.apps.InventoryConfig',
    'apps.matching.apps.MatchingConfig',
    'apps.ussd.apps.UssdConfig',
    'apps.notifications.apps.NotificationsConfig',
    'apps.analytics.apps.AnalyticsConfig',
    'apps.dashboard.apps.DashboardConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'venomlink.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'apps.dashboard.context_processors.venomlink_global_context',
            ],
        },
    },
]

WSGI_APPLICATION = 'venomlink.wsgi.application'
ASGI_APPLICATION = 'venomlink.asgi.application'

# Database
# Defaults to SQLite for immediate, zero-friction local and CI runs.
# In production PostgreSQL, DATABASE_URL=postgresql://user:password@host:port/dbname
DATABASE_URL = os.getenv('DATABASE_URL', '')

if DATABASE_URL.startswith(('postgres://', 'postgresql://')):
    # PostgreSQL Configuration (credentials are URL-decoded; never logged)
    import urllib.parse
    url = urllib.parse.urlparse(DATABASE_URL)
    _query = urllib.parse.parse_qs(url.query)
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': urllib.parse.unquote(url.path[1:]),
            'USER': urllib.parse.unquote(url.username or ''),
            'PASSWORD': urllib.parse.unquote(url.password or ''),
            'HOST': url.hostname or 'localhost',
            'PORT': str(url.port or 5432),
            'CONN_MAX_AGE': int(os.getenv('DB_CONN_MAX_AGE', 60)),
            'OPTIONS': {
                # e.g. DATABASE_URL=postgresql://u:p@host/db?sslmode=require
                'sslmode': _query.get('sslmode', [os.getenv('DB_SSLMODE', 'prefer')])[0],
            },
            'TEST': {'NAME': os.getenv('DB_TEST_NAME', 'test_' + urllib.parse.unquote(url.path[1:]))},
        }
    }
elif not DEBUG and not _env_bool('ALLOW_SQLITE_IN_PRODUCTION', False):
    raise ImproperlyConfigured(
        "DATABASE_URL (PostgreSQL) is required when DEBUG is off. "
        "SQLite is not supported for production because it has no row-level locking."
    )
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

# Cache (used for rate-limit counters). 'db' shares counters across worker processes.
if os.getenv('CACHE_BACKEND', 'locmem').lower() == 'db':
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.db.DatabaseCache',
            'LOCATION': 'venomlink_cache',
        }
    }
else:
    CACHES = {
        'default': {
            'BACKEND': 'django.core.cache.backends.locmem.LocMemCache',
            'LOCATION': 'venomlink-default',
        }
    }

# Password validation
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
]

# Internationalization
# Primary operational language: English. Local time: West Africa Time (Lagos / Abuja)
LANGUAGE_CODE = 'en-gb'
TIME_ZONE = 'Africa/Lagos'
USE_I18N = True
USE_TZ = True

# Static files (CSS, JavaScript, Images)
STATIC_URL = '/static/'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATIC_ROOT = BASE_DIR / 'staticfiles'
# In production run `collectstatic`; WhiteNoise then serves STATIC_ROOT (no finders, no autorefresh).
WHITENOISE_USE_FINDERS = DEBUG
WHITENOISE_AUTOREFRESH = DEBUG

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# REST Framework configuration
_DRF_RENDERERS = ['rest_framework.renderers.JSONRenderer']
if DEBUG:
    # Browsable API reveals forms/serializer details; only enabled in development.
    _DRF_RENDERERS.append('rest_framework.renderers.BrowsableAPIRenderer')

REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.SessionAuthentication',
        'rest_framework.authentication.BasicAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticatedOrReadOnly',
    ],
    'DEFAULT_RENDERER_CLASSES': _DRF_RENDERERS,
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.FormParser',
        'rest_framework.parsers.MultiPartParser',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 25,
    # Number of trusted reverse proxies in front of the app (for correct client-IP throttling).
    'NUM_PROXIES': int(os.getenv('NUM_PROXIES')) if os.getenv('NUM_PROXIES') else None,
}

# Rate limits (see apps/facilities/throttling.py). Format: "<count>/<sec|min|hour|day>"
VENOMLINK_THROTTLE_RATES = {
    'search_anon': os.getenv('THROTTLE_SEARCH_ANON', '60/min'),
    'pin_auth': os.getenv('THROTTLE_PIN_AUTH', '10/min'),
    'ussd_simulator': os.getenv('THROTTLE_USSD_SIMULATOR', '60/min'),
}

# CORS settings for API clients
CORS_ALLOW_ALL_ORIGINS = False
CORS_ALLOWED_ORIGINS = _env_list('CORS_ALLOWED_ORIGINS', 'http://localhost:8000,http://127.0.0.1:8000' if DEBUG else '')
CORS_ALLOW_CREDENTIALS = False

# CSRF / session cookie hardening (always on)
CSRF_COOKIE_HTTPONLY = False  # dashboard JS reads the token for fetch(); header-based CSRF is enforced
CSRF_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = 'Lax'
SESSION_COOKIE_AGE = int(os.getenv('SESSION_COOKIE_AGE', 60 * 60 * 8))  # 8-hour staff sessions
CSRF_TRUSTED_ORIGINS = _env_list('CSRF_TRUSTED_ORIGINS', '')
X_FRAME_OPTIONS = 'DENY'
SECURE_CONTENT_TYPE_NOSNIFF = True
SECURE_REFERRER_POLICY = 'same-origin'

# Production-only transport security (DEBUG off)
if not DEBUG:
    SECURE_SSL_REDIRECT = _env_bool('SECURE_SSL_REDIRECT', True)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    # Raise HSTS only after HTTPS has been validated in staging (see docs/DEPLOYMENT.md)
    SECURE_HSTS_SECONDS = int(os.getenv('SECURE_HSTS_SECONDS', 3600))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = _env_bool('SECURE_HSTS_INCLUDE_SUBDOMAINS', False)
    SECURE_HSTS_PRELOAD = _env_bool('SECURE_HSTS_PRELOAD', False)
    # Only trust X-Forwarded-Proto when the app is reachable exclusively through a proxy that sets it.
    if _env_bool('TRUST_PROXY_SSL_HEADER', False):
        SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    # Health checks must work over plain HTTP from the local proxy / orchestrator.
    SECURE_REDIRECT_EXEMPT = [r'^healthz/$', r'^readyz/$']

# Logging: console only (collected by the process manager). Request bodies, PINs and
# gateway tokens are never logged by application code.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'standard': {'format': '%(asctime)s %(levelname)s %(name)s: %(message)s'},
    },
    'handlers': {
        'console': {'class': 'logging.StreamHandler', 'formatter': 'standard'},
    },
    'root': {'handlers': ['console'], 'level': os.getenv('LOG_LEVEL', 'INFO')},
    'loggers': {
        'django.request': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
        'django.security': {'handlers': ['console'], 'level': 'WARNING', 'propagate': False},
    },
}

LOGIN_URL = '/accounts/login/'
LOGIN_REDIRECT_URL = '/'
LOGOUT_REDIRECT_URL = '/accounts/login/'

# VenomLink Operational Parameters (Configurable via Environment)
VENOMLINK_CONFIG = {
    'STOCK_OUT_THRESHOLD': int(os.getenv('STOCK_OUT_THRESHOLD', 0)),
    'CRITICAL_DAYS_OF_COVER_THRESHOLD': float(os.getenv('CRITICAL_DAYS_OF_COVER_THRESHOLD', 5.0)),
    'WARNING_DAYS_OF_COVER_THRESHOLD': float(os.getenv('WARNING_DAYS_OF_COVER_THRESHOLD', 7.0)),
    # Operational planning assumption for minimum single-course antivenom reserve; requires clinical validation.
    'MIN_TREATMENT_COURSE_VIALS': int(os.getenv('MIN_TREATMENT_COURSE_VIALS', 5)),
    'RESUPPLY_LEAD_TIME_DAYS': int(os.getenv('DEFAULT_RESUPPLY_LEAD_TIME_DAYS', 3)),
    'SAFETY_BUFFER_DAYS': int(os.getenv('DEFAULT_SAFETY_BUFFER_DAYS', 2)),
    'STALE_DATA_THRESHOLD_HOURS': int(os.getenv('STALE_DATA_THRESHOLD_HOURS', 48)),
    'EXPIRY_WARNING_DAYS': int(os.getenv('EXPIRY_WARNING_DAYS', 60)),
    'MIN_CONSUMPTION_EVENTS_HIGH_CONFIDENCE': int(os.getenv('MIN_CONSUMPTION_EVENTS_HIGH_CONFIDENCE', 3)),
    'MIN_OBSERVATION_DAYS_HIGH_CONFIDENCE': int(os.getenv('MIN_OBSERVATION_DAYS_HIGH_CONFIDENCE', 7)),
    'USSD_SESSION_TIMEOUT_SECONDS': int(os.getenv('USSD_SESSION_TIMEOUT_SECONDS', 180)),
    'USSD_MAX_PIN_ATTEMPTS': int(os.getenv('USSD_MAX_PIN_ATTEMPTS', 3)),
    'USSD_LOCKOUT_MINUTES': int(os.getenv('USSD_LOCKOUT_MINUTES', 15)),
    'USSD_GATEWAY_TOKEN': os.getenv('USSD_GATEWAY_TOKEN', ''),
    # Demo mode exposes the USSD simulator, the 90s demo runner, public official-dashboard preview,
    # and the demo notice banner. MUST be False for a pilot / production deployment.
    'SYSTEM_DEMO_MODE': _env_bool('DEMO_MODE', DEBUG),
}

# Africa's Talking (SMS). Empty credentials => SMS runs in SIMULATED mode (no external call).
AFRICASTALKING_USERNAME = os.getenv('AT_USERNAME', '')
AFRICASTALKING_API_KEY = os.getenv('AT_API_KEY', '')
AFRICASTALKING_SENDER_ID = os.getenv('AT_SENDER_ID', '')
AFRICASTALKING_SMS_URL = os.getenv(
    'AT_SMS_URL',
    'https://api.sandbox.africastalking.com/version1/messaging'
    if os.getenv('AT_USERNAME', '') == 'sandbox'
    else 'https://api.africastalking.com/version1/messaging'
)
AFRICASTALKING_TIMEOUT_SECONDS = int(os.getenv('AT_TIMEOUT_SECONDS', 10))


