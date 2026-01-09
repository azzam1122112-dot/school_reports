# config/settings.py
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

# ----------------- البيئة -----------------
def _env_bool(name: str, default: bool = False) -> bool:
    val = os.getenv(name)
    if val is None:
        return default
    return str(val).strip().lower() in {"1", "true", "yes", "on"}

def _split_env_list(val: str) -> list[str]:
    return [x.strip() for x in (val or "").split(",") if x.strip()]

ENV = os.getenv("ENV", "development").strip().lower()

# ----------------- Logging (early) -----------------
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO))
logger = logging.getLogger(__name__)

# ----------------- SECRET_KEY -----------------
SECRET_KEY = (os.getenv("SECRET_KEY") or "").strip()

# ----------------- Celery Broker URL -----------------
# في Render، إذا لم يكن لديك Redis، سيتم استخدام Threading تلقائياً بفضل التعديلات الأخيرة
CELERY_BROKER_URL = os.getenv("REDIS_URL", os.getenv("CELERY_BROKER_URL", "")).strip()

# كشف تلقائي لـ Render
if os.getenv("RENDER") or os.getenv("RENDER_EXTERNAL_URL"):
    ENV = "production"

logger.info("Current Environment: %s", ENV)

# يمكنك أيضًا فرض DEBUG عبر متغير DEBUG=1
DEBUG = (ENV != "production") if os.getenv("DEBUG") is None else _env_bool("DEBUG", False)

logger.info("DEBUG: %s", DEBUG)

if ENV == "production":
    # في الإنتاج لا نسمح بفال باك غير آمن أبدًا
    if not SECRET_KEY or SECRET_KEY == "unsafe-secret":
        raise ImproperlyConfigured("SECRET_KEY must be set to a strong unique value in production.")

    # لا نسمح بـ DEBUG في الإنتاج حتى لو تم ضبطه بالخطأ عبر ENV
    if DEBUG:
        raise ImproperlyConfigured("DEBUG must be False in production.")
else:
    # للتطوير فقط: نوفر قيمة افتراضية حتى لا يتوقف المشروع محليًا
    if not SECRET_KEY:
        SECRET_KEY = "unsafe-secret"

def _default_allowed_hosts() -> list[str]:
    hosts: list[str] = ["localhost", "127.0.0.1"]

    # Known deployed domains (backwards compatible)
    hosts += [
        "school-7lgm.onrender.com",
        "school-reports.onrender.com",
    ]

    # Render external URL (preferred, supports renames without wildcards)
    render_url = (os.getenv("RENDER_EXTERNAL_URL") or "").strip()
    if render_url:
        try:
            parts = urlsplit(render_url)
            if parts.netloc:
                hosts.append(parts.netloc)
        except Exception:
            pass

    # De-dupe while preserving order
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
    """Safer defaults: explicit origins only (no wildcards unless configured via env)."""
    origins: list[str] = []

    # Known deployed domains (kept for backwards compatibility)
    origins += [
        "https://school-7lgm.onrender.com",
        "https://school-reports.onrender.com",
    ]

    # Render external URL (if available)
    render_url = (os.getenv("RENDER_EXTERNAL_URL") or "").strip()
    if render_url:
        try:
            parts = urlsplit(render_url)
            if parts.scheme and parts.netloc:
                origins.append(f"{parts.scheme}://{parts.netloc}")
        except Exception:
            pass

    # De-dupe while preserving order
    seen = set()
    out: list[str] = []
    for o in origins:
        if o and o not in seen:
            seen.add(o)
            out.append(o)
    return out

_csrf_env = (os.getenv("CSRF_TRUSTED_ORIGINS") or "").strip()
CSRF_TRUSTED_ORIGINS = _split_env_list(_csrf_env) if _csrf_env else _default_csrf_trusted_origins()

# ----------------- التطبيقات -----------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",

    # طرف ثالث
    "django_celery_results",

    # تطبيقاتنا
    "reports",
]

# ----------------- Storage Backend (optional R2) -----------------
# إذا كانت متغيرات R2 موجودة، نستخدم Cloudflare R2 لتخزين ملفات الوسائط (media).
# ملاحظة مهمة: قيمة R2_ENDPOINT_URL يجب أن تكون مثل:
#   https://<accountid>.r2.cloudflarestorage.com
# وليس مع اسم الـ bucket في آخر الرابط.
# NOTE: Support common alias env var names (some dashboards use shorter keys).
R2_ACCESS_KEY_ID = (os.getenv("R2_ACCESS_KEY_ID") or os.getenv("R2_ACCESS_KEY") or "").strip()
R2_SECRET_ACCESS_KEY = (os.getenv("R2_SECRET_ACCESS_KEY") or os.getenv("R2_SECRET_KEY") or "").strip()
R2_BUCKET_NAME = (os.getenv("R2_BUCKET_NAME") or "").strip()
R2_ENDPOINT_URL = (
    os.getenv("R2_ENDPOINT_URL")
    or os.getenv("R2_ENDPOINT")
    or os.getenv("Default_Endpoint")
    or ""
).strip()

# بعض واجهات Cloudflare تعرض S3 API مع اسم الـ bucket في نهاية الرابط.
# مثال: https://<accountid>.r2.cloudflarestorage.com/<bucket>
# هنا نطبع الرابط تلقائياً ليصبح endpoint بدون path، ونستخرج اسم الـ bucket عند الحاجة.
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
            # strip path/query/fragment for endpoint
            _r2_effective_endpoint = urlunsplit((parts.scheme, parts.netloc, "", "", ""))
    except Exception:
        pass

# استخدم القيم المطَبَّعة لباقي الإعدادات
R2_BUCKET_NAME = _r2_effective_bucket
R2_ENDPOINT_URL = _r2_effective_endpoint

_use_r2 = bool(R2_ACCESS_KEY_ID and R2_SECRET_ACCESS_KEY and R2_BUCKET_NAME and R2_ENDPOINT_URL)
if _use_r2 and "storages" not in INSTALLED_APPS:
    INSTALLED_APPS.append("storages")

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",  # لملفات static
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "reports.middleware_single_session.EnforceSingleSessionMiddleware",
    "reports.middleware.AuditLogMiddleware",  # <--- تم الإضافة
    "reports.middleware.IdleLogoutMiddleware",  # تسجيل خروج تلقائي بعد الخمول
    "reports.middleware.SubscriptionMiddleware",  # <--- تم الإضافة
    "reports.middleware.ReportViewerAccessMiddleware",  # حسابات عرض فقط (مشرف تقارير)
    "reports.middleware.ContentSecurityPolicyMiddleware",  # CSP (production hardening)
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

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
                # متوافق مع الأيقونة/الهيدر
                "reports.context_processors.nav_badges",
                "reports.context_processors.csp",
            ],
        },
    },
]

WSGI_APPLICATION = "config.wsgi.application"

# ----------------- Redis URLs (Broker/Cache) -----------------
# افصل الكاش عن الـ broker إن أمكن لتقليل التداخل.
REDIS_CACHE_URL = os.getenv("REDIS_CACHE_URL", "").strip()

def _derive_cache_redis_url(broker_url: str) -> str:
    """Derive a cache Redis URL from broker URL by switching DB index when possible."""
    if not broker_url:
        return ""
    try:
        parts = urlsplit(broker_url)
        # Path is usually like /0
        path = (parts.path or "/0").strip()
        if path.startswith("/"):
            path_num = path[1:]
        else:
            path_num = path
        # Only adjust if numeric
        if path_num.isdigit():
            db = int(path_num)
            # common practice: broker DB 0, cache DB 1
            if db == 0:
                new_path = "/1"
            else:
                new_path = f"/{db + 1}"
            return urlunsplit((parts.scheme, parts.netloc, new_path, parts.query, parts.fragment))
        return broker_url
    except Exception:
        return broker_url

if not REDIS_CACHE_URL:
    REDIS_CACHE_URL = _derive_cache_redis_url(CELERY_BROKER_URL)

# ----------------- الكاش (Caching) -----------------
# - في الإنتاج: نفضل Redis إن توفر، وإلا نستخدم LocMem (أفضل من كسر الإقلاع).
# - في التطوير: نستخدم LocMem إذا لم يوجد Redis.
if REDIS_CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django_redis.cache.RedisCache",
            "LOCATION": REDIS_CACHE_URL,
            "OPTIONS": {
                "CLIENT_CLASS": "django_redis.client.DefaultClient",
                "IGNORE_EXCEPTIONS": True,  # تجاهل أخطاء الاتصال بـ Redis
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

# ----------------- قاعدة البيانات -----------------
# الأولوية لـ DATABASE_URL إن وُجد وكان dj_database_url متاحًا
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
DB_SSL = _env_bool("DB_SSL", False)

if DATABASE_URL and dj_database_url:
    # يدعم Postgres و MySQL إلخ عبر URL واحد
    DATABASES = {
        "default": dj_database_url.parse(
            DATABASE_URL,
            conn_max_age=0,  # تقليل العمر لتجنب SSL SYSCALL error على Render
            ssl_require=DB_SSL,
        )
    }
else:
    # تكوين يدوي عبر متغيرات منفصلة أو fallback إلى SQLite
    DB_ENGINE = os.getenv("DB_ENGINE", "django.db.backends.sqlite3").strip()
    DB_NAME   = os.getenv("DB_NAME", "").strip()
    DB_USER   = os.getenv("DB_USER", "").strip()
    DB_PASS   = os.getenv("DB_PASSWORD", "").strip()
    DB_HOST   = os.getenv("DB_HOST", "").strip()
    DB_PORT   = os.getenv("DB_PORT", "5432").strip()

    if "sqlite" in DB_ENGINE.lower() or not (DB_NAME and DB_ENGINE and (DB_HOST or "sqlite" in DB_ENGINE.lower())):
        DATABASES = {
            "default": {
                "ENGINE": "django.db.backends.sqlite3",
                "NAME": os.getenv("DB_NAME", BASE_DIR / "db.sqlite3"),
            }
        }
    else:
        # تهيئة Postgres (أو أي backend آخر تحدده) من المتغيرات الفردية
        engine = DB_ENGINE
        if DB_ENGINE.startswith("postgres") or DB_ENGINE.endswith("postgresql"):
            engine = "django.db.backends.postgresql"
        DATABASES = {
            "default": {
                "ENGINE": engine,
                "NAME": DB_NAME,
                "USER": DB_USER,
                "PASSWORD": DB_PASS,
                "HOST": DB_HOST,   # تأكد أنه FQDN كامل (مثال: xxx.oregon-postgres.render.com)
                "PORT": DB_PORT,
                "CONN_MAX_AGE": 600,
                "OPTIONS": {"sslmode": "require"} if DB_SSL and "postgresql" in engine else {},
            }
        }

# خلف Proxy (مثل Render) حافظ على HTTPS + اسم المضيف الأصلي
if ENV == "production":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    USE_X_FORWARDED_HOST = True
else:
    SECURE_PROXY_SSL_HEADER = None
    USE_X_FORWARDED_HOST = False

# ----------------- كلمات المرور -----------------
AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

# ----------------- اللغة والتوقيت -----------------
LANGUAGE_CODE = "ar"
TIME_ZONE = "Asia/Riyadh"
USE_I18N = True
USE_TZ = True

# ----------------- Celery Configuration -----------------
CELERY_RESULT_BACKEND = "django-db"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = TIME_ZONE
CELERY_TASK_TRACK_STARTED = True
CELERY_TASK_TIME_LIMIT = 30 * 60  # 30 minutes

# ----------------- الملفات الثابتة -----------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]  # هنا يوجد img/logo.png

# WhiteNoise في الإنتاج
if ENV == "production":
    STATICFILES_STORAGE = "whitenoise.storage.CompressedManifestStaticFilesStorage"
    WHITENOISE_MAX_AGE = 60 * 60 * 24 * 365  # 1 سنة

# ----------------- ملفات الوسائط -----------------
MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

# ----------------- حدود رفع البيانات -----------------
# لتجنّب TooManyFieldsSent في لوحة الإدارة عند تنفيذ عمليات جماعية
# (مثل تحديد عدد كبير من السجلات أو صفحات تحتوي حقول كثيرة).
# يمكن التحكم بالقيمة عبر ENV: DATA_UPLOAD_MAX_NUMBER_FIELDS
DATA_UPLOAD_MAX_NUMBER_FIELDS = int(os.getenv("DATA_UPLOAD_MAX_NUMBER_FIELDS", "20000"))

# حد أقصى لحجم الـ request body بالبايت لتقليل مخاطر DoS (قابل للتعديل عبر ENV).
# ملاحظة: رفع الملفات الكبيرة عبر multipart يحتسب ضمن هذا الحد.
DATA_UPLOAD_MAX_MEMORY_SIZE = int(os.getenv("DATA_UPLOAD_MAX_MEMORY_SIZE", str(40 * 1024 * 1024)))

# ----------------- Cloudflare R2 (شرطي) -----------------
# عند تفعيل R2، سيكون هو التخزين الافتراضي.
# اختياري:
# - R2_PUBLIC_DOMAIN: دومين عام لعرض الملفات (مثل custom domain أو *.r2.dev)
#   يمكن إدخاله كـ host فقط (media.example.com) أو كـ URL كامل (https://media.example.com)
# - AWS_QUERYSTRING_AUTH: إن كانت الـ bucket private وتحتاج روابط موقعة اجعله 1
R2_PUBLIC_DOMAIN = (os.getenv("R2_PUBLIC_DOMAIN") or "").strip()
if R2_PUBLIC_DOMAIN:
    # Normalize: allow passing full URL; storages expects host without scheme.
    try:
        parts = urlsplit(R2_PUBLIC_DOMAIN)
        if parts.scheme and parts.netloc:
            R2_PUBLIC_DOMAIN = parts.netloc
    except Exception:
        pass
    # Also handle values provided as host/path without scheme.
    # Example: pub-xxx.r2.dev/school-reports -> pub-xxx.r2.dev
    R2_PUBLIC_DOMAIN = R2_PUBLIC_DOMAIN.strip().strip("/")
    if "/" in R2_PUBLIC_DOMAIN:
        R2_PUBLIC_DOMAIN = R2_PUBLIC_DOMAIN.split("/", 1)[0]

if _use_r2:
    DEFAULT_FILE_STORAGE = "reports.storage.R2MediaStorage"

    AWS_ACCESS_KEY_ID = R2_ACCESS_KEY_ID
    AWS_SECRET_ACCESS_KEY = R2_SECRET_ACCESS_KEY
    AWS_STORAGE_BUCKET_NAME = R2_BUCKET_NAME
    AWS_S3_ENDPOINT_URL = R2_ENDPOINT_URL

    # Cloudflare R2 best-practice settings
    AWS_S3_REGION_NAME = os.getenv("AWS_S3_REGION_NAME", "auto")
    AWS_S3_SIGNATURE_VERSION = os.getenv("AWS_S3_SIGNATURE_VERSION", "s3v4")
    AWS_S3_ADDRESSING_STYLE = os.getenv("AWS_S3_ADDRESSING_STYLE", "path")
    AWS_DEFAULT_ACL = None
    # If no public domain is configured, default to signed URLs so media can still display.
    # NOTE: When using signed URLs, we should NOT use AWS_S3_CUSTOM_DOMAIN; signatures are bound to host.
    AWS_QUERYSTRING_AUTH = _env_bool("AWS_QUERYSTRING_AUTH", not bool(R2_PUBLIC_DOMAIN))
    AWS_QUERYSTRING_EXPIRE = int(os.getenv("AWS_QUERYSTRING_EXPIRE", "86400"))
    AWS_S3_FILE_OVERWRITE = _env_bool("AWS_S3_FILE_OVERWRITE", False)
    AWS_S3_OBJECT_PARAMETERS = {
        "CacheControl": os.getenv("AWS_S3_CACHE_CONTROL", "max-age=31536000"),
    }
    if R2_PUBLIC_DOMAIN and not AWS_QUERYSTRING_AUTH:
        AWS_S3_CUSTOM_DOMAIN = R2_PUBLIC_DOMAIN

# ملاحظة: حقل المرفق في Ticket يستخدم PublicRawMediaStorage صراحةً من reports/storage.py

# ----------------- الأمان في الإنتاج -----------------
if ENV == "production":
    SECURE_SSL_REDIRECT = True
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SESSION_COOKIE_HTTPONLY = True
    CSRF_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    CSRF_COOKIE_SAMESITE = os.getenv("CSRF_COOKIE_SAMESITE", "Lax")

    # ----------------- CSP (Content Security Policy) -----------------
    # ملاحظة: القوالب الحالية تستخدم inline <style>/<script> لذا نبدأ بسياسة
    # متوافقة (مع unsafe-inline) ويمكن لاحقاً التحول إلى nonce/hashes.
    CSP_ENABLED = _env_bool("CSP_ENABLED", True)
    CSP_REPORT_ONLY = _env_bool("CSP_REPORT_ONLY", False)
    CONTENT_SECURITY_POLICY = (os.getenv("CONTENT_SECURITY_POLICY") or "").strip()

    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = os.getenv("SECURE_REFERRER_POLICY", "strict-origin-when-cross-origin")
    SECURE_CROSS_ORIGIN_OPENER_POLICY = os.getenv("SECURE_CROSS_ORIGIN_OPENER_POLICY", "same-origin")

    SECURE_HSTS_SECONDS = int(os.getenv("SECURE_HSTS_SECONDS", "31536000"))  # سنة
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    X_FRAME_OPTIONS = "DENY"
else:
    SECURE_SSL_REDIRECT = False

    # CSP off by default in development to avoid hindering iteration
    CSP_ENABLED = _env_bool("CSP_ENABLED", False)
    CSP_REPORT_ONLY = _env_bool("CSP_REPORT_ONLY", True)
    CONTENT_SECURITY_POLICY = (os.getenv("CONTENT_SECURITY_POLICY") or "").strip()

# ----------------- تسجيل الأحداث (Logging) -----------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": LOG_LEVEL},
    "loggers": {
        "django.request": {"handlers": ["console"], "level": LOG_LEVEL, "propagate": False},
    },
}

# ----------------- المستخدم المخصص -----------------
AUTH_USER_MODEL = "reports.Teacher"

# توجيه افتراضي
LOGIN_URL = "reports:login"
LOGIN_REDIRECT_URL = "reports:home"
LOGOUT_REDIRECT_URL = "reports:login"

# ----------------- الخمول/الجلسات -----------------
# 30 دقيقة خمول => تسجيل خروج عند أول طلب بعد انتهاء المدة.
# يمكن تعديلها عبر متغير البيئة IDLE_LOGOUT_SECONDS
IDLE_LOGOUT_SECONDS = int(os.getenv("IDLE_LOGOUT_SECONDS", str(30 * 60)))

# جلسة منزلقة (sliding): أي تفاعل يعيد ضبط مؤقت الجلسة
SESSION_COOKIE_AGE = IDLE_LOGOUT_SECONDS
# لا نريد تمديد الجلسة مع أي طلب (خصوصاً polling/AJAX).
# الـ IdleLogoutMiddleware يقوم بتحديث الصلاحية عند التفاعل فقط.
SESSION_SAVE_EVERY_REQUEST = False

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# كيف نتصرف عند وجود أكثر من رئيس للقسم؟
# "blank"  => ترك خانة الاعتماد فارغة
# "dept"   => طباعة اسم القسم فقط مع خط فارغ للتوقيع
PRINT_MULTIHEAD_POLICY = "blank"  # أو "dept"

# كيف نحدد رؤساء القسم؟
DEPARTMENT_HEAD_ROLE_SLUG = "department_head"  # غيّرها لو اسم السلاج مختلف

SITE_URL = (os.getenv("SITE_URL") or "").strip()
if not SITE_URL:
    _render_url = (os.getenv("RENDER_EXTERNAL_URL") or "").strip()
    SITE_URL = _render_url or "https://school-reports.onrender.com"
