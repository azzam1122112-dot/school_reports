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


def _media_querystring_auth_enabled(
    *,
    public_access_enabled: bool,
    requested_querystring_auth: bool,
) -> bool:
    """Never allow unsigned media URLs unless public access is explicit."""
    return (not public_access_enabled) or bool(requested_querystring_auth)


# ----------------- Environment -----------------
ENV = os.getenv("ENV", "development").strip().lower()

# يمكنك أيضًا فرض DEBUG عبر DEBUG=1
DEBUG = (ENV != "production") if os.getenv("DEBUG") is None else _env_bool("DEBUG", False)

# في الإنتاج نمنع السقوط على backends غير موزعة (SQLite/LocMem/InMemory) بشكل افتراضي.
PRODUCTION_STRICT_MODE = _env_bool("PRODUCTION_STRICT_MODE", ENV == "production")

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
        "app.tawtheeq-ksa.com",
        "tawtheeq-ksa.com",
        "www.tawtheeq-ksa.com",
    ]

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
        "https://app.tawtheeq-ksa.com",
        "https://tawtheeq-ksa.com",
        "https://www.tawtheeq-ksa.com",
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
CSRF_FAILURE_VIEW = "core.views.csrf_failure"


# ----------------- Share Links (public, no-account) -----------------
try:
    SHARE_LINK_DEFAULT_DAYS = int(os.getenv("SHARE_LINK_DEFAULT_DAYS", "7").strip() or "7")
except Exception:
    SHARE_LINK_DEFAULT_DAYS = 7

# Public security contact exposed by /.well-known/security.txt.
SECURITY_CONTACT_EMAIL = (
    os.getenv("SECURITY_CONTACT_EMAIL") or "support@tawtheeq-ksa.com"
).strip()

# ----------------- Mansour public AI assistant -----------------
# The secret is server-side only. The widget remains visible without it and
# reports a safe temporary-unavailable message until production is configured.
OPENAI_API_KEY = (os.getenv("OPENAI_API_KEY") or "").strip()
MANSOUR_ASSISTANT_ENABLED = _env_bool(
    "MANSOUR_ASSISTANT_ENABLED",
    bool(OPENAI_API_KEY),
)
MANSOUR_ASSISTANT_MODEL = (
    os.getenv("MANSOUR_ASSISTANT_MODEL") or "gpt-5-mini"
).strip()

_mansour_reasoning_effort = (
    os.getenv("MANSOUR_ASSISTANT_REASONING_EFFORT") or "minimal"
).strip().lower()
if _mansour_reasoning_effort not in {"minimal", "low", "medium", "high"}:
    _mansour_reasoning_effort = "minimal"
MANSOUR_ASSISTANT_REASONING_EFFORT = _mansour_reasoning_effort

try:
    MANSOUR_ASSISTANT_MAX_OUTPUT_TOKENS = max(
        100,
        min(900, int(os.getenv("MANSOUR_ASSISTANT_MAX_OUTPUT_TOKENS", "700"))),
    )
except (TypeError, ValueError):
    MANSOUR_ASSISTANT_MAX_OUTPUT_TOKENS = 700

try:
    MANSOUR_ASSISTANT_TIMEOUT_SECONDS = max(
        5.0,
        min(30.0, float(os.getenv("MANSOUR_ASSISTANT_TIMEOUT_SECONDS", "20"))),
    )
except (TypeError, ValueError):
    MANSOUR_ASSISTANT_TIMEOUT_SECONDS = 20.0


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


# ----------------- Telegram operational alerts -----------------
TELEGRAM_ALERTS_ENABLED = _env_bool("TELEGRAM_ALERTS_ENABLED", False)
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
TELEGRAM_ALERT_CHAT_ID = (os.getenv("TELEGRAM_ALERT_CHAT_ID") or "").strip()
TELEGRAM_ALERT_CATEGORIES = set(
    _split_env_list(
        os.getenv(
            "TELEGRAM_ALERT_CATEGORIES",
            "support,subscriptions,registration,payments,complaints",
        )
    )
)
try:
    TELEGRAM_ALERT_TIMEOUT_SECONDS = float(
        (os.getenv("TELEGRAM_ALERT_TIMEOUT_SECONDS", "10") or "10").strip()
    )
except Exception:
    TELEGRAM_ALERT_TIMEOUT_SECONDS = 10.0
try:
    TELEGRAM_ALERT_DEDUP_TTL_SECONDS = int(
        (os.getenv("TELEGRAM_ALERT_DEDUP_TTL_SECONDS", "2592000") or "2592000").strip()
    )
except Exception:
    TELEGRAM_ALERT_DEDUP_TTL_SECONDS = 2_592_000


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
    "maintenance",
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
    "core.middleware.RequestTraceMiddleware",
    "core.middleware.BlockBadPathsMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "reports.middleware.CanonicalHostMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "reports.middleware_single_session.EnforceSingleSessionMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "reports.middleware.AuditLogMiddleware",
    "reports.middleware.MaintenanceModeMiddleware",
    "reports.middleware.SearchEngineIndexingMiddleware",
    "reports.middleware.IdleLogoutMiddleware",
    "reports.middleware.ActiveSchoolGuardMiddleware",
    "reports.middleware.SubscriptionMiddleware",
    "reports.middleware.ForcePasswordChangeMiddleware",
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
                "reports.context_processors.seo",
            ],
        },
    },
]


# ----------------- WSGI/ASGI -----------------
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"


# ----------------- Redis URLs (Broker/Cache/Channels) -----------------
REDIS_URL = os.getenv("REDIS_URL", "").strip()

# Celery broker: يفضل نفس REDIS_URL إن ما عندك غيره
CELERY_BROKER_URL = (os.getenv("CELERY_BROKER_URL") or REDIS_URL).strip()

# Cache Redis URL: لو ما انكتب، نشتقه من broker بتغيير DB index
REDIS_CACHE_URL = os.getenv("REDIS_CACHE_URL", "").strip()
REDIS_CHANNEL_LAYER_URL = (os.getenv("REDIS_CHANNEL_LAYER_URL") or "").strip() or REDIS_URL

# Database URL is needed by strict production checks below.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()


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

if ENV == "production" and PRODUCTION_STRICT_MODE:
    if not DATABASE_URL:
        raise ImproperlyConfigured("DATABASE_URL is required in production when PRODUCTION_STRICT_MODE is enabled.")
    if not REDIS_URL:
        raise ImproperlyConfigured("REDIS_URL is required in production when PRODUCTION_STRICT_MODE is enabled.")


# ----------------- Caching -----------------
# ── Redis scaling notes ─────────────────────────────────────────
# Current: single Redis instance — DB 0 (broker + channels), DB 1 (cache).
# Adequate up to ~500 schools.  Split thresholds:
#   - Cache memory > 200 MB  → separate REDIS_CACHE_URL instance
#   - Celery queue depth > 1000 for >5 min → separate broker instance
#   - WS connections > 5K concurrent → separate channels Redis
# Monitor: redis INFO memory, connected_clients, used_memory_rss.
#
# ── Key prefix map (Phase 6E) ──────────────────────────────────
# Prefix       DB   Purpose
# sr:*         1    Django cache (views, opmetrics, sessions, locks)
# celery*      0    Celery broker (task queues + results)
# asgi:*       0    Channels layer (WebSocket groups)
# opmetrics:*  1    Operational counters (via cache backend)
# To split: set separate REDIS_CACHE_URL / CELERY_BROKER_URL / channel hosts.
# ────────────────────────────────────────────────────────────────
if REDIS_CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_CACHE_URL,
            "KEY_PREFIX": "sr",
            "TIMEOUT": 300,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "IGNORE_EXCEPTIONS": True,
            },
        }
    }
else:
    if ENV == "production" and PRODUCTION_STRICT_MODE:
        raise ImproperlyConfigured("REDIS_CACHE_URL is required in production when PRODUCTION_STRICT_MODE is enabled.")
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
    if ENV == "production" and PRODUCTION_STRICT_MODE:
        raise ImproperlyConfigured("REDIS_CHANNEL_LAYER_URL is required in production when PRODUCTION_STRICT_MODE is enabled.")
    # NOTE: InMemory مناسب للتجربة فقط (لا يصلح لعدة نسخ/سيرفرات)
    CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}


# ----------------- Database -----------------
# ── Scaling notes ───────────────────────────────────────────────
# Current: single PostgreSQL, CONN_MAX_AGE=600, 3 web workers × 2 threads
# = up to 6 persistent connections per web dyno.
# At 500+ schools: consider PgBouncer or another managed connection pooling layer.
# At 1000+ schools: evaluate read replica for nav_context / dashboard queries.
# Hot tables: NotificationRecipient, AuditLog, Report (see docs/PHASE5 report).
#
# ── PgBouncer readiness (Phase 6D) ─────────────────────────────
# Recommended PgBouncer settings when adding a connection pooler:
#   pool_mode = transaction          (required: Django uses SET/RESET per query)
#   default_pool_size = 20           (per-user pool, tune to DB max_connections)
#   max_client_conn = 200            (allow all workers + web + beat to connect)
#   server_idle_timeout = 300        (match CONN_MAX_AGE / 2)
#   server_lifetime = 3600
# Django side:
#   - Set CONN_MAX_AGE=0 (let PgBouncer manage pooling, not Django)
#   - Or keep CONN_MAX_AGE=600 if using session mode (not recommended)
#   - Point DATABASE_URL to PgBouncer host:port instead of Postgres directly
# Rollback: revert DATABASE_URL to direct Postgres endpoint, restore CONN_MAX_AGE
# ────────────────────────────────────────────────────────────────
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
    if ENV == "production" and PRODUCTION_STRICT_MODE:
        raise ImproperlyConfigured("DATABASE_URL must be configured in production; SQLite fallback is disabled.")
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


# خلف Proxy في الإنتاج: حافظ على HTTPS + اسم المضيف الأصلي
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
# ── Worker scaling notes ────────────────────────────────────────
# Current: 1 worker, concurrency=4, all queues on one process.
# Scaling path:
#   100 schools  → current config is fine
#   500 schools  → separate worker for 'images' queue (CPU-bound)
#   1000 schools → separate workers per queue; fan-out daily summary
# Monitor: celery inspect active, flower, or ops/metrics endpoint.
# ────────────────────────────────────────────────────────────────
CELERY_RESULT_BACKEND = (os.getenv("CELERY_RESULT_BACKEND") or REDIS_CACHE_URL or CELERY_BROKER_URL or "django-db").strip()
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = False
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes
CELERY_WORKER_PREFETCH_MULTIPLIER = int(os.getenv("CELERY_PREFETCH_MULTIPLIER", "1"))
CELERY_TASK_ACKS_LATE = _env_bool("CELERY_TASK_ACKS_LATE", True)
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_RESULT_EXPIRES = int(os.getenv("CELERY_RESULT_EXPIRES", "3600"))  # 1 hour

# ── Queue routing ───────────────────────────────────────────────────
# Logical queue separation so image processing cannot starve notifications.
# All queues resolve to the same broker; to run dedicated workers per queue,
# start additional workers with  -Q notifications  or  -Q images  etc.
# The default worker (no -Q flag) consumes ALL queues, so this is backward-
# compatible and requires zero deployment changes.
from kombu import Queue  # noqa: E402

CELERY_TASK_QUEUES = [
    Queue("default"),
    Queue("notifications"),
    Queue("images"),
    Queue("periodic"),
]
CELERY_TASK_DEFAULT_QUEUE = "default"
CELERY_TASK_ROUTES = {
    "reports.tasks.send_telegram_alert_task": {"queue": "notifications"},
    "reports.tasks.send_notification_task": {"queue": "notifications"},
    "reports.tasks.send_password_change_email_task": {"queue": "notifications"},
    "reports.tasks.process_report_images": {"queue": "images"},
    "reports.tasks.process_ticket_image": {"queue": "images"},
    "reports.tasks.send_daily_manager_summary_task": {"queue": "periodic"},
    "reports.tasks._daily_summary_for_school": {"queue": "periodic"},
    "reports.tasks.check_subscription_expiry_task": {"queue": "periodic"},
    "reports.tasks.remind_unsigned_circulars_task": {"queue": "periodic"},
    "reports.tasks.cleanup_audit_logs_task": {"queue": "periodic"},
}


# ----------------- Audit Logs Retention -----------------
AUDIT_LOG_RETENTION_DAYS = int(os.getenv("AUDIT_LOG_RETENTION_DAYS", "30"))
AUDIT_LOG_CLEANUP_ENABLED = _env_bool("AUDIT_LOG_CLEANUP_ENABLED", True)


# ----------------- Daily Manager Report -----------------
DAILY_MANAGER_REPORT_ENABLED = _env_bool("DAILY_MANAGER_REPORT_ENABLED", True)
DAILY_MANAGER_REPORT_INAPP_ENABLED = _env_bool("DAILY_MANAGER_REPORT_INAPP_ENABLED", True)
DAILY_MANAGER_REPORT_EMAIL_ENABLED = _env_bool("DAILY_MANAGER_REPORT_EMAIL_ENABLED", True)
DAILY_MANAGER_REPORT_WHATSAPP_ENABLED = _env_bool("DAILY_MANAGER_REPORT_WHATSAPP_ENABLED", False)

try:
    DAILY_MANAGER_REPORT_HOUR = int((os.getenv("DAILY_MANAGER_REPORT_HOUR", "16") or "16").strip())
except Exception:
    DAILY_MANAGER_REPORT_HOUR = 16
DAILY_MANAGER_REPORT_HOUR = max(0, min(23, DAILY_MANAGER_REPORT_HOUR))

try:
    DAILY_MANAGER_REPORT_MINUTE = int((os.getenv("DAILY_MANAGER_REPORT_MINUTE", "5") or "5").strip())
except Exception:
    DAILY_MANAGER_REPORT_MINUTE = 5
DAILY_MANAGER_REPORT_MINUTE = max(0, min(59, DAILY_MANAGER_REPORT_MINUTE))

DAILY_MANAGER_REPORT_DAY_OF_WEEK = (os.getenv("DAILY_MANAGER_REPORT_DAY_OF_WEEK", "thu") or "thu").strip().lower()
if DAILY_MANAGER_REPORT_DAY_OF_WEEK in {"thursday", "thur", "khamis"}:
    DAILY_MANAGER_REPORT_DAY_OF_WEEK = "thu"
if DAILY_MANAGER_REPORT_DAY_OF_WEEK in {"*", "all", "daily"}:
    DAILY_MANAGER_REPORT_DAY_OF_WEEK = "*"

DAILY_MANAGER_REPORT_WHATSAPP_WEBHOOK_URL = (os.getenv("DAILY_MANAGER_REPORT_WHATSAPP_WEBHOOK_URL") or "").strip()
DAILY_MANAGER_REPORT_WHATSAPP_WEBHOOK_TOKEN = (os.getenv("DAILY_MANAGER_REPORT_WHATSAPP_WEBHOOK_TOKEN") or "").strip()

try:
    DAILY_MANAGER_REPORT_WHATSAPP_TIMEOUT_SECONDS = float(
        (os.getenv("DAILY_MANAGER_REPORT_WHATSAPP_TIMEOUT_SECONDS", "10") or "10").strip()
    )
except Exception:
    DAILY_MANAGER_REPORT_WHATSAPP_TIMEOUT_SECONDS = 10.0


# ----------------- Subscription Expiry Reminders -----------------
SUBSCRIPTION_EXPIRY_REMINDER_ENABLED = _env_bool("SUBSCRIPTION_EXPIRY_REMINDER_ENABLED", True)
# أيام التنبيه قبل انتهاء الاشتراك (قائمة مفصولة بفواصل)
try:
    SUBSCRIPTION_EXPIRY_REMINDER_DAYS = [
        int(d.strip()) for d in (os.getenv("SUBSCRIPTION_EXPIRY_REMINDER_DAYS", "14,7,3,1") or "14,7,3,1").split(",") if d.strip()
    ]
except Exception:
    SUBSCRIPTION_EXPIRY_REMINDER_DAYS = [14, 7, 3, 1]
SUBSCRIPTION_EXPIRY_REMINDER_EMAIL_ENABLED = _env_bool("SUBSCRIPTION_EXPIRY_REMINDER_EMAIL_ENABLED", False)


# ----------------- Unsigned Circular Reminders -----------------
CIRCULAR_SIGNATURE_REMINDER_ENABLED = _env_bool("CIRCULAR_SIGNATURE_REMINDER_ENABLED", True)
# تذكير قبل N ساعة من الموعد النهائي (افتراضي: 48 و 24)
try:
    CIRCULAR_SIGNATURE_REMINDER_HOURS = [
        int(h.strip()) for h in (os.getenv("CIRCULAR_SIGNATURE_REMINDER_HOURS", "48,24") or "48,24").split(",") if h.strip()
    ]
except Exception:
    CIRCULAR_SIGNATURE_REMINDER_HOURS = [48, 24]


# ----------------- Password Change Email Confirmation -----------------
PASSWORD_CHANGE_EMAIL_ENABLED = _env_bool("PASSWORD_CHANGE_EMAIL_ENABLED", True)


# ----------------- Email -----------------
EMAIL_BACKEND = (
    os.getenv("EMAIL_BACKEND")
    or ("django.core.mail.backends.console.EmailBackend" if ENV != "production" else "django.core.mail.backends.smtp.EmailBackend")
).strip()
EMAIL_HOST = (os.getenv("EMAIL_HOST") or "localhost").strip()
try:
    EMAIL_PORT = int((os.getenv("EMAIL_PORT", "25") or "25").strip())
except Exception:
    EMAIL_PORT = 25
EMAIL_HOST_USER = (os.getenv("EMAIL_HOST_USER") or "").strip()
EMAIL_HOST_PASSWORD = (os.getenv("EMAIL_HOST_PASSWORD") or "").strip()
EMAIL_USE_TLS = _env_bool("EMAIL_USE_TLS", False)
EMAIL_USE_SSL = _env_bool("EMAIL_USE_SSL", False)
DEFAULT_FROM_EMAIL = (os.getenv("DEFAULT_FROM_EMAIL") or "no-reply@tawtheeq-ksa.com").strip()
try:
    EMAIL_TIMEOUT = max(
        3,
        int((os.getenv("EMAIL_TIMEOUT", "15") or "15").strip()),
    )
except (TypeError, ValueError):
    EMAIL_TIMEOUT = 15
try:
    PASSWORD_RESET_TIMEOUT = max(
        300,
        int((os.getenv("PASSWORD_RESET_TIMEOUT", "3600") or "3600").strip()),
    )
except (TypeError, ValueError):
    PASSWORD_RESET_TIMEOUT = 3600

try:
    from celery.schedules import crontab
except Exception:  # pragma: no cover
    crontab = None  # type: ignore

CELERY_BEAT_SCHEDULE: dict[str, dict] = {}
if crontab is not None:
    if AUDIT_LOG_CLEANUP_ENABLED:
        CELERY_BEAT_SCHEDULE["cleanup-audit-logs-daily"] = {
            "task": "reports.tasks.cleanup_audit_logs_task",
            "schedule": crontab(minute=15, hour=3),
            "args": (AUDIT_LOG_RETENTION_DAYS,),
        }

    if DAILY_MANAGER_REPORT_ENABLED:
        CELERY_BEAT_SCHEDULE["send-daily-manager-summary"] = {
            "task": "reports.tasks.send_daily_manager_summary_task",
            "schedule": crontab(
                minute=DAILY_MANAGER_REPORT_MINUTE,
                hour=DAILY_MANAGER_REPORT_HOUR,
                day_of_week=DAILY_MANAGER_REPORT_DAY_OF_WEEK,
            ),
        }

    if SUBSCRIPTION_EXPIRY_REMINDER_ENABLED:
        CELERY_BEAT_SCHEDULE["check-subscription-expiry-daily"] = {
            "task": "reports.tasks.check_subscription_expiry_task",
            "schedule": crontab(minute=30, hour=8),  # يومياً الساعة 8:30 صباحاً
        }

    if CIRCULAR_SIGNATURE_REMINDER_ENABLED:
        CELERY_BEAT_SCHEDULE["remind-unsigned-circulars"] = {
            "task": "reports.tasks.remind_unsigned_circulars_task",
            "schedule": crontab(minute=0, hour="8,14"),  # مرتين يومياً: 8 صباحاً و 2 ظهراً
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
FILE_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("FILE_UPLOAD_MAX_MEMORY_SIZE", str(2 * 1024 * 1024)))
DATA_UPLOAD_MAX_NUMBER_FILES = int(os.getenv("DATA_UPLOAD_MAX_NUMBER_FILES", "20"))


# ----------------- Cloudflare R2 (conditional) -----------------
# Uploaded school files are private by default. Direct public media URLs must be
# enabled explicitly because reports, tickets, circulars, achievement evidence,
# and payment receipts may contain sensitive data.
MEDIA_PUBLIC_ACCESS_ENABLED = _env_bool("MEDIA_PUBLIC_ACCESS_ENABLED", False)
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

    # Private is the safe default. When public media is disabled, ignore any
    # stale AWS_QUERYSTRING_AUTH=0 value and always issue expiring signed URLs.
    AWS_QUERYSTRING_AUTH = _media_querystring_auth_enabled(
        public_access_enabled=MEDIA_PUBLIC_ACCESS_ENABLED,
        requested_querystring_auth=_env_bool("AWS_QUERYSTRING_AUTH", False),
    )
    AWS_QUERYSTRING_EXPIRE = int(os.getenv("AWS_QUERYSTRING_EXPIRE", "86400"))
    AWS_S3_FILE_OVERWRITE = _env_bool("AWS_S3_FILE_OVERWRITE", True)

    AWS_S3_OBJECT_PARAMETERS = {
        "CacheControl": os.getenv("AWS_S3_CACHE_CONTROL", "max-age=31536000"),
    }

    if MEDIA_PUBLIC_ACCESS_ENABLED and R2_PUBLIC_DOMAIN and not AWS_QUERYSTRING_AUTH:
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
        "django.server": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "django.db.backends": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "channels": {"handlers": ["console"], "level": "WARNING", "propagate": False},
        "daphne": {"handlers": ["console"], "level": "WARNING", "propagate": False},
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

# ── Session backend ─────────────────────────────────────────────────
# cached_db: reads from cache (fast), writes to both cache + DB.
# If a cache key expires (TIMEOUT=300), Django transparently falls back
# to the DB row — so users are never logged out by cache eviction.
# Pure "cache" backend would lose sessions when Redis keys expire.
if REDIS_CACHE_URL:
    SESSION_ENGINE = "django.contrib.sessions.backends.cached_db"
    SESSION_CACHE_ALIAS = "default"
else:
    SESSION_ENGINE = "django.contrib.sessions.backends.db"


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
        "rest_framework.throttling.AnonRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "user": "120/min",
        "anon": "30/min",
    },
}

# ----------------- Misc -----------------
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

PRINT_MULTIHEAD_POLICY = "blank"  # أو "dept"
DEPARTMENT_HEAD_ROLE_SLUG = "department_head"

SITE_URL = (os.getenv("SITE_URL") or "").strip()
if not SITE_URL:
    SITE_URL = "https://tawtheeq-ksa.com" if ENV == "production" else "http://127.0.0.1:8000"
SITE_URL = SITE_URL.rstrip("/")

# Optional business settings retained for internal configuration. Public
# templates do not render these values.
BUSINESS_LEGAL_NAME = (os.getenv("BUSINESS_LEGAL_NAME") or "").strip()
BUSINESS_COMMERCIAL_REGISTRATION = (
    os.getenv("BUSINESS_COMMERCIAL_REGISTRATION") or ""
).strip()
BUSINESS_FREELANCE_DOCUMENT_NUMBER = (
    os.getenv("BUSINESS_FREELANCE_DOCUMENT_NUMBER") or ""
).strip()
BUSINESS_FREELANCE_ACTIVITY = (
    os.getenv("BUSINESS_FREELANCE_ACTIVITY") or ""
).strip()
BUSINESS_FREELANCE_DOCUMENT_EXPIRY = (
    os.getenv("BUSINESS_FREELANCE_DOCUMENT_EXPIRY") or ""
).strip()
BUSINESS_FREELANCE_DOCUMENT_URL = (
    os.getenv("BUSINESS_FREELANCE_DOCUMENT_URL") or ""
).strip()
BUSINESS_TAX_NUMBER = (os.getenv("BUSINESS_TAX_NUMBER") or "").strip()
BUSINESS_LICENSES = (os.getenv("BUSINESS_LICENSES") or "").strip()
BUSINESS_VERIFICATION_URL = (os.getenv("BUSINESS_VERIFICATION_URL") or "").strip()
BUSINESS_ADDRESS = (os.getenv("BUSINESS_ADDRESS") or "").strip()
BUSINESS_SUPPORT_EMAIL = (os.getenv("BUSINESS_SUPPORT_EMAIL") or "").strip()
BUSINESS_SUPPORT_PHONE = (os.getenv("BUSINESS_SUPPORT_PHONE") or "").strip()
CANONICAL_HOST_REDIRECT = _env_bool(
    "CANONICAL_HOST_REDIRECT",
    ENV == "production",
)

# Self-service school trial. The free plan exposes the complete product journey
# while keeping teacher count and archive storage deliberately small.
TRIAL_DAYS = max(1, int(os.getenv("TRIAL_DAYS", "14") or "14"))
TRIAL_PLAN_NAME = (os.getenv("TRIAL_PLAN_NAME") or "تجربة مجانية").strip()
TRIAL_MAX_TEACHERS = max(1, int(os.getenv("TRIAL_MAX_TEACHERS", "5") or "5"))
TRIAL_ARCHIVE_STORAGE_GB = max(
    1,
    int(os.getenv("TRIAL_ARCHIVE_STORAGE_GB", "1") or "1"),
)
