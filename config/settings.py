# config/settings.py
from __future__ import annotations

from pathlib import Path
import os
import logging
from urllib.parse import urlsplit, urlunsplit

from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

# حاول استخدام dj_database_url إن كان مُثبتًا، بدون كسر المشروع لو غير موجود
try:
    import dj_database_url  # type: ignore
except Exception:
    dj_database_url = None  # type: ignore

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent


# ----------------- Helpers -----------------
def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}


def _split_env_list(val: str) -> list[str]:
    return [x.strip() for x in (val or "").split(",") if x.strip()]


# ----------------- Environment -----------------
ENV = os.getenv("ENV", "development").strip().lower()

# كشف تلقائي لـ Render (الأقوى من ENV اليدوي)
if os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"):
    ENV = "production"

# يمكنك أيضًا فرض DEBUG عبر DEBUG=1
DEBUG = (ENV != "production") if os.getenv("DEBUG") is None else _env_bool("DEBUG", False)

# ----------------- Logging (early) -----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)

logger.info("Current Environment: %s", ENV)
logger.info("DEBUG: %s", DEBUG)


# ----------------- SECRET_KEY -----------------
SECRET_KEY = (os.getenv("SECRET_KEY") or "").strip()

if ENV == "production":
    if not SECRET_KEY or SECRET_KEY == "unsafe-secret":
        raise ImproperlyConfigured("SECRET_KEY must be set to a strong unique value in production.")
    if DEBUG:
        raise ImproperlyConfigured("DEBUG must be False in production.")
else:
    # للتطوير فقط
    if not SECRET_KEY:
        SECRET_KEY = "unsafe-secret"


# ----------------- Allowed Hosts / CSRF Trusted Origins -----------------
def _default_allowed_hosts() -> list[str]:
    hosts: list[str] = ["localhost", "127.0.0.1"]

    # Known deployed domains (backwards compatible)
    hosts += [
        "school-7lgm.onrender.com",
        "school-reports.onrender.com",
        "app.tawtheeq-ksa.com",
        "tawtheeq-ksa.com",
    ]

    # Render external URL (preferred)
    render_url = (os.getenv("RENDER_EXTERNAL_URL") or "").strip()
    if render_url:
        try:
            parts = urlsplit(render_url)
            if parts.netloc:
                hosts.append(parts.netloc)
        except Exception:
            pass

    # De-dupe
    seen = set()
    out: list[str] = []
    for h in hosts:
        if h and h not in seen:
            seen.add(h)
            out.append(h)
    return out


_allowed_hosts_env = (os.getenv("ALLOWED_HOSTS") or "").strip()
ALLOWED_HOSTS = _split_env_list(_allowed_hosts_env) if _allowed_hosts_env else _default_allowed_hosts()


def _default_csrf_trusted_origins() -> list[str]:
    origins: list[str] = []

    # Static known origins (backwards compatible)
    origins += [
        "https://school-7lgm.onrender.com",
        "https://school-reports.onrender.com",
        "https://app.tawtheeq-ksa.com",
        "https://tawtheeq-ksa.com",
    ]

    # Derive trusted origins from allowed hosts to reduce host-mismatch CSRF issues.
    for host in ALLOWED_HOSTS:
        h = (host or "").strip()
        if not h or h in {"*", "."}:
            continue
        if h.startswith("."):
            # Django CSRF trusted origins requires explicit origins, skip wildcard-like host.
            continue
        # Production-like domains: https
        origins.append(f"https://{h}")
        # Local/dev convenience
        if h in {"localhost", "127.0.0.1", "[::1]"}:
            origins.append(f"http://{h}")

    render_url = (os.getenv("RENDER_EXTERNAL_URL") or "").strip()
    if render_url:
        try:
            parts = urlsplit(render_url)
            if parts.scheme and parts.netloc:
                origins.append(f"{parts.scheme}://{parts.netloc}")
        except Exception:
            pass

    # De-dupe
    seen = set()
    out: list[str] = []
    for o in origins:
        if o and o not in seen:
            seen.add(o)
            out.append(o)
    return out


_csrf_env = (os.getenv("CSRF_TRUSTED_ORIGINS") or "").strip()
CSRF_TRUSTED_ORIGINS = _split_env_list(_csrf_env) if _csrf_env else _default_csrf_trusted_origins()


# ----------------- Share Links (public, no-account) -----------------
try:
    SHARE_LINK_DEFAULT_DAYS = int(os.getenv("SHARE_LINK_DEFAULT_DAYS", "7").strip() or "7")
except Exception:
    SHARE_LINK_DEFAULT_DAYS = 7


# ----------------- Notifications: Local fallback (no broker) -----------------
NOTIFICATIONS_LOCAL_FALLBACK_ENABLED = _env_bool("NOTIFICATIONS_LOCAL_FALLBACK_ENABLED", True)
NOTIFICATIONS_LOCAL_FALLBACK_THREAD = _env_bool("NOTIFICATIONS_LOCAL_FALLBACK_THREAD", True)

try:
    NOTIFICATIONS_LOCAL_FALLBACK_MAX_RECIPIENTS = int(
        (os.getenv("NOTIFICATIONS_LOCAL_FALLBACK_MAX_RECIPIENTS", "30") or "30").strip()
    )
except Exception:
    NOTIFICATIONS_LOCAL_FALLBACK_MAX_RECIPIENTS = 30

try:
    NOTIFICATIONS_LOCAL_FALLBACK_HARD_STOP_RECIPIENTS = int(
        (os.getenv("NOTIFICATIONS_LOCAL_FALLBACK_HARD_STOP_RECIPIENTS", "200") or "200").strip()
    )
except Exception:
    NOTIFICATIONS_LOCAL_FALLBACK_HARD_STOP_RECIPIENTS = 200

try:
    NOTIFICATIONS_LOCAL_FALLBACK_WARN_SECONDS = float(
        (os.getenv("NOTIFICATIONS_LOCAL_FALLBACK_WARN_SECONDS", "2") or "2").strip()
    )
except Exception:
    NOTIFICATIONS_LOCAL_FALLBACK_WARN_SECONDS = 2.0

try:
    NOTIFICATIONS_DISPATCH_LOCK_TTL_SECONDS = int(
        (os.getenv("NOTIFICATIONS_DISPATCH_LOCK_TTL_SECONDS", "3600") or "3600").strip()
    )
except Exception:
    NOTIFICATIONS_DISPATCH_LOCK_TTL_SECONDS = 3600


# ----------------- Short-TTL DB Load Shedding -----------------
try:
    NAV_CONTEXT_CACHE_TTL_SECONDS = int(os.getenv("NAV_CONTEXT_CACHE_TTL_SECONDS", "20").strip() or "20")
except Exception:
    NAV_CONTEXT_CACHE_TTL_SECONDS = 20

try:
    UNREAD_COUNT_CACHE_TTL_SECONDS = int(os.getenv("UNREAD_COUNT_CACHE_TTL_SECONDS", "15").strip() or "15")
except Exception:
    UNREAD_COUNT_CACHE_TTL_SECONDS = 15


# ----------------- Applications -----------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.humanize",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    # Third-party
    "channels",
    "django_celery_results",
    "rest_framework",
    # Our apps
    "core",
    "reports",
]


# ----------------- Storage Backend (optional R2) -----------------
R2_ACCESS_KEY_ID = (os.getenv("R2_ACCESS_KEY_ID") or os.getenv("R2_ACCESS_KEY") or "").strip()
R2_SECRET_ACCESS_KEY = (os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("R2_SECRET_KEY") or "").strip()
R2_BUCKET_NAME = (os.getenv("R2_BUCKET_NAME") or "").strip()
R2_ENDPOINT_URL = (
    os.getenv("R2_ENDPOINT_URL")
    or os.getenv("R2_ENDPOINT")
    or os.getenv("Default_Endpoint")
    or ""
).strip()

_r2_effective_bucket = R2_BUCKET_NAME
_r2_effective_endpoint = R2_ENDPOINT_URL
if R2_ENDPOINT_URL:
    try:
        parts = urlsplit(R2_ENDPOINT_URL)
        path = (parts.path or "").strip("/")
        if path:
            bucket_from_path = path.split("/", 1)[0]
            if not _r2_effective_bucket:
                _r2_effective_bucket = bucket_from_path
            _r2_effective_endpoint = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    except Exception:
        pass

R2_BUCKET_NAME = _r2_effective_bucket
R2_ENDPOINT_URL = _r2_effective_endpoint

_use_r2 = bool(R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME and R2_ENDPOINT_URL)
if _use_r2 and "storages" not in INSTALLED_APPS:
    INSTALLED_APPS.append("storages")


# ----------------- Middleware -----------------
MIDDLEWARE = [
    "core.middleware.BlockBadPathsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "reports.middleware_single_session.EnforceSingleSessionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "reports.middleware.AuditLogMiddleware",
    "reports.middleware.IdleLogoutMiddleware",
    "reports.middleware.ActiveSchoolGuardMiddleware",
    "reports.middleware.SubscriptionMiddleware",
    "reports.middleware.PlatformAdminAccessMiddleware",
    "reports.middleware.ReportViewerAccessMiddleware",
    "reports.middleware.ContentSecurityPolicyMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]


# ----------------- URLs / Templates -----------------
ROOT_URLCONF = "config.urls"

# ✅ تم إصلاح سبب الخطأ: حذف المفتاح الغلط (" reed")
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
                "reports.context_processors.nav_context",
                "reports.context_processors.csp",
            ],
        },
    },
]


# ----------------- WSGI/ASGI -----------------
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# ----------------- Redis URLs (Broker/Cache/Channels) -----------------
# Render: استخدم REDIS_URL من Key Value الداخلي
REDIS_URL = os.getenv("REDIS_URL", "").strip()

# Celery broker: يفضل نفس REDIS_URL إن ما عندك غيره
CELERY_BROKER_URL = (os.getenv("CELERY_BROKER_URL") or REDIS_URL).strip()

# Cache Redis URL: لو ما انكتب، نشتقه من broker بتغيير DB index
REDIS_CACHE_URL = os.getenv("REDIS_CACHE_URL", "").strip()
REDIS_CHANNEL_LAYER_URL = (os.getenv("REDIS_CHANNEL_LAYER_URL") or "").strip() or REDIS_URL


def _derive_cache_redis_url(broker_url: str) -> str:
    if not broker_url:
        return ""
    try:
        parts = urlsplit(broker_url)
        path = (parts.path or "/0").strip()
        path_num = path[1:] if path.startswith("/") else path
        if path_num.isdigit():
            db = int(path_num)
            new_path = "/1" if db == 0 else f"/{db + 1}"
            return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))
        return broker_url
    except Exception:
        return broker_url


if not REDIS_CACHE_URL:
    REDIS_CACHE_URL = _derive_cache_redis_url(CELERY_BROKER_URL)


# ----------------- Caching -----------------
if REDIS_CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_CACHE_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "IGNORE_EXCEPTIONS": True,
            },
        }
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "school_reports_locmem",
        }
    }


# ----------------- Channels Layer -----------------
if REDIS_CHANNEL_LAYER_URL:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {"hosts": [REDIS_CHANNEL_LAYER_URL]},
        }
    }
else:
    # NOTE: InMemory مناسب للتجربة فقط (لا يصلح لعدة نسخ/سيرفرات)
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


# ----------------- Database -----------------
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_SSL = _env_bool("DB_SSL", False)

# الحد الأقصى لعمر الاتصال (ثوانٍ). 0 يعني إغلاق الاتصال بعد كل طلب.
# 600 (10 دقائق) يُحسّن الأداء بشكل ملحوظ مع عدد كبير من المدارس.
_CONN_MAX_AGE = int(os.getenv("CONN_MAX_AGE", "600"))

if DATABASE_URL and dj_database_url:
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=_CONN_MAX_AGE,
            ssl_require=DB_SSL,
        )
    }
else:
    DB_ENGINE = os.getenv("DB_ENGINE", "django.db.backends.sqlite3").strip()
    DB_NAME = os.getenv("DB_NAME", "").strip()
    DB_USER = os.getenv("DB_USER", "").strip()
    DB_PASS = os.getenv("DB_PASSWORD", "").strip()
    DB_HOST = os.getenv("DB_HOST", "").strip()
    DB_PORT = os.getenv("DB_PORT", "5432").strip()

    if "sqlite" in DB_ENGINE.lower() or not (DB_NAME and DB_ENGINE and (DB_HOST or "sqlite" in DB_ENGINE.lower())):
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": os.getenv("DB_NAME", BASE_DIR / "db.sqlite3"),
            }
        }
    else:
        engine = DB_ENGINE
        if DB_ENGINE.startswith("postgres") or DB_ENGINE.endswith("postgresql"):
            engine = "django.db.backends.postgresql"
        DATABASES = {
            "default": {
                "ENGINE": engine,
                "NAME": DB_NAME,
                "USER": DB_USER,
                "PASSWORD": DB_PASS,
                "HOST": DB_HOST,
                "PORT": DB_PORT,
                "CONN_MAX_AGE": 600,
                "OPTIONS": {"sslmode": "require"} if DB_SSL and "postgresql" in engine else {},
            }
        }


# خلف Proxy (Render) حافظ على HTTPS + اسم المضيف الأصلي
if ENV == "production":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
else:
    SECURE_PROXY_SSL_HEADER = None
    USE_X_FORWARDED_HOST = False


# ----------------- Password Validators -----------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


# ----------------- I18N / TZ -----------------
LANGUAGE_CODE = "ar"
TIME_ZONE = "Asia/Riyadh"
USE_I18N = True
USE_TZ = True


# ----------------- Celery -----------------
CELERY_RESULT_BACKEND = "django-db"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes


# ----------------- Audit Logs Retention -----------------
AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "30"))
AUDIT_LOG_CLEANUP_ENABLED = _env_bool("AUDIT_LOG_CLEANUP_ENABLED", True)

try:
    from celery.schedules import crontab
except Exception:  # pragma: no cover
    crontab = None  # type: ignore

if AUDIT_LOG_CLEANUP_ENABLED and crontab is not None:
    CELERY_BEAT_SCHEDULE = {
        "cleanup-audit-logs-daily": {
            "task": "reports.tasks.cleanup_audit_logs_task",
            "schedule": crontab(minute=15, hour=3),
            "args": (AUDIT_LOG_RETENTION_DAYS,),
        }
    }


# ----------------- Static files -----------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]

if ENV == "production":
    STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
    WHITENOISE_MAX_AGE = 60 * 60 * 24 * 365


# ----------------- Media -----------------
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"


# ----------------- Upload limits -----------------
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(os.getenv("DATA_UPLOAD_MAX_NUMBER_FIELDS", "20000"))
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", str(40 * 1024 * 1024)))


# ----------------- Cloudflare R2 (conditional) -----------------
R2_PUBLIC_DOMAIN = (os.getenv("R2_PUBLIC_DOMAIN") or "").strip()
if R2_PUBLIC_DOMAIN:
    try:
        parts = urlsplit(R2_PUBLIC_DOMAIN)
        if parts.scheme and parts.netloc:
            R2_PUBLIC_DOMAIN = parts.netloc
    except Exception:
        pass
    R2_PUBLIC_DOMAIN = R2_PUBLIC_DOMAIN.strip().strip("/")
    if "/" in R2_PUBLIC_DOMAIN:
        R2_PUBLIC_DOMAIN = R2_PUBLIC_DOMAIN.split("/", 1)[0]

if _use_r2:
    DEFAULT_FILE_STORAGE = "reports.storage.R2MediaStorage"

    AWS_ACCESS_KEY_ID = R2_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY = R2_SECRET_ACCESS_KEY
    AWS_STORAGE_BUCKET_NAME = R2_BUCKET_NAME
    AWS_S3_ENDPOINT_URL = R2_ENDPOINT_URL

    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "auto")
    AWS_S3_SIGNATURE_VERSION = os.getenv("AWS_S3_SIGNATURE_VERSION", "s3v4")
    AWS_S3_ADDRESSING_STYLE = os.getenv("AWS_S3_ADDRESSING_STYLE", "path")
    AWS_DEFAULT_ACL = None

    AWS_QUERYSTRING_AUTH = _env_bool("AWS_QUERYSTRING_AUTH", not bool(R2_PUBLIC_DOMAIN))
    AWS_QUERYSTRING_EXPIRE = int(os.getenv("AWS_QUERYSTRING_EXPIRE", "86400"))
    AWS_S3_FILE_OVERWRITE = _env_bool("AWS_S3_FILE_OVERWRITE", True)

    AWS_S3_OBJECT_PARAMETERS = {
        "CacheControl": os.getenv("AWS_S3_CACHE_CONTROL", "max-age=31536000"),
    }

    if R2_PUBLIC_DOMAIN and not AWS_QUERYSTRING_AUTH:
        AWS_S3_CUSTOM_DOMAIN = R2_PUBLIC_DOMAIN


# ----------------- Security (production) -----------------
if ENV == "production":
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    CSRF_COOKIE_SAMESITE = os.getenv("CSRF_COOKIE_SAMESITE", "Lax")

    CSP_ENABLED = _env_bool("CSP_ENABLED", True)
    CSP_REPORT_ONLY = _env_bool("CSP_REPORT_ONLY", False)
    CONTENT_SECURITY_POLICY = (os.getenv("CONTENT_SECURITY_POLICY") or "").strip()

    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = os.getenv("SECURE_REFERRER_POLICY", "strict-origin-when-cross-origin")
    SECURE_CROSS_ORIGIN_OPENER_POLICY = os.getenv("SECURE_CROSS_ORIGIN_OPENER_POLICY", "same-origin")

    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = "DENY"
else:
    SECURE_SSL_REDIRECT = False
    CSP_ENABLED = _env_bool("CSP_ENABLED", False)
    CSP_REPORT_ONLY = _env_bool("CSP_REPORT_ONLY", True)
    CONTENT_SECURITY_POLICY = (os.getenv("CONTENT_SECURITY_POLICY") or "").strip()


# ----------------- Logging (Django) -----------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": "ERROR", "propagate": False},
    },
}


# ----------------- Custom User / Auth redirects -----------------
AUTH_USER_MODEL = "reports.Teacher"

LOGIN_URL = "reports:login"
LOGIN_REDIRECT_URL = "reports:home"
LOGOUT_REDIRECT_URL = "reports:login"

MESSAGE_STORAGE = "django.contrib.messages.storage.cookie.CookieStorage"


# ----------------- Sessions / Idle logout -----------------
IDLE_LOGOUT_SECONDS = int(os.getenv("IDLE_LOGOUT_SECONDS", str(30 * 60)))

SESSION_COOKIE_AGE = IDLE_LOGOUT_SECONDS
SESSION_SAVE_EVERY_REQUEST = False


# ----------------- Django REST Framework -----------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework.authentication.SessionAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "rest_framework.pagination.PageNumberPagination",
    "PAGE_SIZE": 25,
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "120/min",
    },
}

# ----------------- Misc -----------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

PRINT_MULTIHEAD_POLICY = "blank"  # أو "dept"
DEPARTMENT_HEAD_ROLE_SLUG = "department_head"

SITE_URL = (os.getenv("SITE_URL") or "").strip()
if not SITE_URL:
    SITE_URL = "https://app.tawtheeq-ksa.com" if ENV == "production" else "http://127.0.0.1:8000"
