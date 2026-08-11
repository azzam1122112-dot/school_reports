# خطة الإصلاح الأمني — منصة توثيق

**المصدر:** [SECURITY_AUDIT_REPORT.md](SECURITY_AUDIT_REPORT.md) · [CRITICAL_FIXES.md](CRITICAL_FIXES.md)
**التاريخ:** 2026-08-11
**المبدأ الحاكم:** لا تغيير معماري، ولا تغيير في منطق العمل إلا حيث يمثّل ثغرة. كل مرحلة تُختم باختبار انحدار يمنع عودة المشكلة.

---

## ✅ ما نُفِّذ فعليًا (2026-08-11)

| المرحلة | البند | الملفات |
|---|---|---|
| 2 | عمر الروابط الموقَّعة → 900s | `config/settings.py` |
| 2 | حارس العزل المركزي (8 اختبارات) | `reports/tests/test_tenant_isolation_matrix.py` |
| 3 | حدّ الدخول يفشل مغلقًا | `reports/views/auth.py` |
| 3 | وقف تسجيل الجوال/الهوية (6 أسطر) | `reports/views/auth.py` |
| 4 | `Sec-Fetch-Site` على المساعد الذكي | `reports/views/mansour.py` |
| 5 | مخزن حدود مستقل `noeviction` | `core/limits_cache.py`, `compose.hetzner.yaml`, `config/settings.py` |
| 5 | تحويل عدّادات الدخول والمستأجر والذكاء الاصطناعي إليه | `auth.py`, `core/middleware.py`, `mansour.py` |
| 6 | احتفاظ سجل التدقيق → 365 يومًا | `config/settings.py` |
| 7 | `preload` + `Permissions-Policy` + `CORP` | `deploy/hetzner/Caddyfile.fragment` |
| 7 | قفل الاعتماديات المُجزَّأ + `--require-hashes` | `requirements.lock.txt`, `Dockerfile` |
| 7 | بوّابات CI: قفل، أسرار، `pip-audit` على القفل | `.github/workflows/ci.yml`, `scripts/` |
| 8 | إزالة CDN من CSP (Chart.js مُستضاف) | `reports/middleware.py`, قالبان, `static/js/vendor/` |
| 8 | حرّاس ما قبل الالتزام | `.pre-commit-config.yaml`, `scripts/check_no_tracked_secrets.py` |

**اختبارات جديدة:** 31 (`test_auth_hardening.py`, `test_security_hardening.py`, `test_tenant_isolation_matrix.py`).
**التحقق:** 1538 اختبارًا OK · `ruff` نظيف · `bandit` صفر عالي · `pip-audit` على القفل نظيف · `check --deploy --fail-level WARNING` نظيف.

**المتبقي بانتظار الخادم:** تدوير `SECRET_KEY` (P0-1) · تفعيل Sentry (P2-4) · توكن `moyasar_callback` (P2-1، مؤجَّل عمدًا).

---

## المرحلة 1 — Critical Security

**الحالة:** ✅ **لا يوجد شيء لتنفيذه.**

الفحص لم يعثر على أي ثغرة Critical: لا تجاوز صلاحيات، لا حقن (SQL/OS/Template/Path)، لا تنفيذ كود عن بُعد، لا انتحال هوية عبر منطق التطبيق، لا تسريب بيانات بين المستأجرين.

هذه المرحلة تبقى في الوثيقة كسِجلّ لما فُحص، لا كعمل معلَّق.

---

## المرحلة 2 — Tenant Isolation

**الحالة:** 🟢 **العزل سليم — العمل هنا تثبيتي لا إصلاحي.**

لم يُعثر على خلل في العزل. الهدف من هذه المرحلة **تحصين ما هو صحيح اليوم ضد الانحدار غدًا**، وإغلاق المسار العابر الوحيد المتبقّي.

### 2.1 — تقليص نافذة روابط الوسائط الموقَّعة (P0-4 · SEC-005)

```bash
# deploy/hetzner/env.production
AWS_QUERYSTRING_EXPIRE=900   # كان 86400
```

**اختبار يدوي إلزامي:** ZIP بيانات المدرسة، إيصال دفع، مرفق تعميم، PDF ملف إنجاز.

### 2.2 — تثبيت العزل باختبارات انحدار صريحة

المشروع يحوي 109 ملف اختبار منها اختبارات أدوار وعزل. أضِف الاختبار التالي كحارس مركزي **لا يعتمد على انضباط المطوّر في العرض القادم**:

```python
# reports/tests/test_tenant_isolation_matrix.py
"""حارس العزل — مصفوفة مدرستين × كل نوع كائن.

الغرض ليس إعادة اختبار ما ثبتت صحته، بل جعل أي انحدار مستقبلي يفشل هنا
قبل أن يصل إلى الإنتاج.
"""
from django.test import TestCase
from django.urls import reverse

from reports.models import Report, School, SchoolMembership, Teacher


class TenantIsolationMatrixTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.school_a = School.objects.create(name="مدرسة أ", is_active=True)
        cls.school_b = School.objects.create(name="مدرسة ب", is_active=True)

        cls.user_a = Teacher.objects.create_user(phone="0500000001", password="Pw!12345aA")
        cls.user_b = Teacher.objects.create_user(phone="0500000002", password="Pw!12345aA")

        SchoolMembership.objects.create(
            teacher=cls.user_a, school=cls.school_a,
            role_type=SchoolMembership.RoleType.MANAGER, is_active=True,
        )
        SchoolMembership.objects.create(
            teacher=cls.user_b, school=cls.school_b,
            role_type=SchoolMembership.RoleType.MANAGER, is_active=True,
        )

        cls.report_b = Report.objects.create(
            school=cls.school_b, teacher=cls.user_b, title="تقرير مدرسة ب",
        )

    def _login_as_a(self):
        self.client.force_login(self.user_a)
        session = self.client.session
        session["active_school_id"] = self.school_a.id
        session.save()

    # ── 1) الوصول المباشر بالمعرِّف ──────────────────────────────
    def test_manager_a_cannot_open_report_of_school_b(self):
        self._login_as_a()
        response = self.client.get(
            reverse("reports:report_detail", args=[self.report_b.pk])
        )
        self.assertIn(response.status_code, (302, 403, 404))

    # ── 2) تزوير المدرسة النشطة في الجلسة ────────────────────────
    def test_forged_active_school_id_is_rejected_by_middleware(self):
        self.client.force_login(self.user_a)
        session = self.client.session
        session["active_school_id"] = self.school_b.id   # مدرسة لا عضوية له فيها
        session.save()

        response = self.client.get(
            reverse("reports:home"), HTTP_ACCEPT="application/json"
        )
        self.assertEqual(response.status_code, 403)
        self.assertEqual(self.client.session.get("active_school_id"), None)

    # ── 3) واجهة REST ────────────────────────────────────────────
    def test_rest_api_never_returns_another_schools_reports(self):
        self._login_as_a()
        response = self.client.get("/api/v1/reports/")
        self.assertEqual(response.status_code, 200)
        returned_ids = {row["id"] for row in response.json().get("results", [])}
        self.assertNotIn(self.report_b.pk, returned_ids)

    # ── 4) عزل الكاش ─────────────────────────────────────────────
    def test_dashboard_cache_keys_carry_the_school_id(self):
        from reports.cache_utils import _dashboard_payload_key
        key_a = _dashboard_payload_key(self.school_a.id, "month", 1)
        key_b = _dashboard_payload_key(self.school_b.id, "month", 1)
        self.assertNotEqual(key_a, key_b)
        self.assertIn(str(self.school_a.id), key_a)
```

**معيار الخروج من المرحلة:** الاختبارات الأربعة تمرّ، والاختبار اليدوي للوسائط يمرّ.

---

## المرحلة 3 — Authentication & Authorization

**الحالة:** 🟠 بند واحد حاجز (P0-2) وآخر عالي الأولوية (P0-3).

المصادقة والصلاحيات **سليمة منطقيًا**. المشكلة في سلوكها عند تعثّر البنية التحتية، وفي ما تكتبه في السجلات.

### 3.1 — إغلاق مسار الفشل المفتوح (P0-2 · SEC-002)

```python
# reports/views/auth.py

# تعذّر التحقق من العدّاد ليس إذنًا بالمرور. حدُّ الـ IP وحده لا يكفي:
# المهاجم الموزَّع يبقيه تحته بينما يجرّب على الحساب الواحد بلا سقف.
_LOGIN_THROTTLE_FAIL_CLOSED = True


def _login_account_locked(identifier: str) -> bool:
    if not identifier:
        return False
    try:
        return int(cache.get(_login_throttle_key(identifier)) or 0) >= LOGIN_ACCOUNT_MAX_FAILURES
    except Exception:
        logger.error("Login throttle store unavailable — failing closed", exc_info=True)
        opmetrics.increment("auth.login.throttle_store_unavailable")
        return bool(_LOGIN_THROTTLE_FAIL_CLOSED)
```

**ملاحظة تشغيلية مهمة:** هذا يعني أن سقوط Redis يمنع الدخول للجميع. هذه مقايضة مقصودة — ولذلك **يجب أن يقترن بالبند 5.1 (مثيل Redis مستقل للحدود)** ليبقى احتمال السقوط منخفضًا جدًا، وبتنبيه فوري على العدّاد `auth.login.throttle_store_unavailable`.

### 3.2 — وقف تسجيل PII (P0-3 · SEC-003)

```python
# reports/views/auth.py — أضِف قبل login_view
def _identifier_for_log(identifier: str) -> str:
    """معرِّف قابل للربط في التحقيقات، غير قابل للعكس إلى بيانات شخصية."""
    return _login_throttle_key(identifier)[-12:] if identifier else "-"
```

ثم استبدل في الأسطر 816 و820 و824: `identifier=%s` → `identifier_hash=%s` و`identifier` → `_identifier_for_log(identifier)`.

### 3.3 — اختبارات الانحدار

```python
# reports/tests/test_auth_hardening.py
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse


class AuthHardeningTests(TestCase):
    def test_login_fails_closed_when_the_throttle_store_is_unavailable(self):
        with patch("reports.views.auth.cache.get", side_effect=ConnectionError("redis down")):
            response = Client().post(
                reverse("reports:login"),
                {"phone": "0500000000", "password": "whatever"},
            )
        self.assertNotEqual(response.status_code, 200)

    def test_failed_login_never_logs_the_raw_identifier(self):
        national_id = "1098765432"
        with self.assertLogs("reports.views.auth", level="WARNING") as captured:
            Client().post(reverse("reports:login"),
                          {"phone": national_id, "password": "wrong-password"})
        joined = "\n".join(captured.output)
        self.assertNotIn(national_id, joined)
        self.assertIn("identifier_hash=", joined)
```

**معيار الخروج:** الاختباران يمرّان، وحزمة اختبارات المصادقة القائمة (`test_password_recovery.py`, `test_passkeys.py`, `test_forced_password_change.py`, `test_login_csrf.py`) تمرّ بلا انحدار.

---

## المرحلة 4 — API Security

**الحالة:** 🟢 سليم — بندان اختياريان للدفاع في العمق.

الـ API مبنيّ بشكل صحيح: `IsAuthenticated` افتراضي عالمي، `IsTenantMember` على كل ViewSet حسّاس، `get_queryset` مفلتر بالمستأجر في كل موضع، Serializers صريحة الحقول، ترقيم صفحات إجباري بـ `PAGE_SIZE=25`، وخنق `120/min` للمستخدم و`30/min` للمجهول.

### 4.1 — توكن مشترك لـ `moyasar_callback` (P2-1 · SEC-008)

```python
# reports/urls.py — أضِف مقطع التوكن إلى المسار
path("payments/moyasar/callback/<str:batch_ref>/<str:token>/",
     subscriptions.moyasar_callback, name="moyasar_callback"),
```

```python
# reports/views/subscriptions.py
import hmac, hashlib

def _moyasar_callback_token(batch_ref: str) -> str:
    secret = str(getattr(settings, "MOYASAR_SECRET_KEY", "") or "")
    return hmac.new(secret.encode(), batch_ref.encode(), hashlib.sha256).hexdigest()[:32]


@require_http_methods(["POST"])
@ratelimit(key="ip", rate="60/m", method="POST", block=True)
def moyasar_callback(request, batch_ref: str, token: str):
    if not moyasar_is_enabled():
        return JsonResponse({"detail": "Moyasar is disabled."}, status=404)
    if not hmac.compare_digest(token, _moyasar_callback_token(batch_ref)):
        # 200 دائمًا: التفريق بين الرموز يكشف وجود المرجع لمن لا يحق له.
        return JsonResponse({"ok": True})
    try:
        invoice_status = _sync_moyasar_batch(batch_ref)
    except (MoyasarGatewayError, ImproperlyConfigured, _ApprovalError):
        logger.exception("Moyasar callback verification failed for batch %s", batch_ref)
        return JsonResponse({"ok": True})
    return JsonResponse({"ok": True, "status": invoice_status})
```

**تنبيه نشر:** حدّث عنوان الـ callback في لوحة Moyasar **قبل** نشر هذا التغيير، وإلا فشلت التفعيلات التلقائية. مهمة `reconcile-pending-gateway-payments` (كل 20 دقيقة) تعمل كشبكة أمان أثناء الانتقال.

### 4.2 — اشتراط نفس الأصل على المساعد الذكي (P2-2 · SEC-009)

```python
# reports/views/mansour.py — بعد فحص content_type
_ALLOWED_FETCH_SITES = {"same-origin", "same-site", "none"}

fetch_site = (request.headers.get("Sec-Fetch-Site") or "").lower()
if fetch_site and fetch_site not in _ALLOWED_FETCH_SITES:
    return _json_response({"ok": False, "message": "طلب غير مسموح."}, status=403)
```

الشرط `if fetch_site` يحافظ على التوافق مع المتصفحات التي لا ترسل الترويسة.

---

## المرحلة 5 — Cache & Redis

**الحالة:** 🟠 أهم مرحلة بعد P0 — تعالج السبب الجذري لـ SEC-002.

Redis آمن شبكيًا بشكل تام (`requirepass`، شبكة `internal`، صفر منافذ منشورة). المشكلة أن **مثيلًا واحدًا يحمل الكاش والجلسات والطوابير وعدّادات الحدود معًا**، و`volatile-lru` يُخلي الأخيرة صامتًا.

### 5.1 — مثيل Redis مستقل لعدّادات الحدود (P1-2)

```yaml
# compose.hetzner.yaml — خدمة جديدة
  redis-limits:
    image: redis:7.4-alpine
    restart: unless-stopped
    security_opt:
      - no-new-privileges:true
    env_file:
      - ${REDIS_ENV_FILE:-deploy/hetzner/env.redis}
    logging: *default-logging
    mem_limit: 128m
    # noeviction عن قصد: عدّاد حدٍّ يُخلى هو حدٌّ اختفى. الحجم صغير جدًا
    # (عدّاد لكل معرِّف/IP بنافذة دقائق) فلا خطر امتلاء عملي.
    command: >-
      sh -c 'exec redis-server
      --maxmemory 96mb
      --maxmemory-policy noeviction
      --requirepass "$${REDIS_PASSWORD}"'
    healthcheck:
      test: ["CMD-SHELL", "redis-cli -a \"$${REDIS_PASSWORD}\" ping | grep PONG"]
      interval: 10s
      timeout: 5s
      retries: 12
    networks:
      - backend
```

```python
# config/settings.py — بعد تعريف CACHES
REDIS_LIMITS_URL = os.getenv("REDIS_LIMITS_URL", "").strip()
if REDIS_LIMITS_URL:
    CACHES["limits"] = {
        "BACKEND": "django_redis.cache.RedisCache",
        "LOCATION": REDIS_LIMITS_URL,
        "KEY_PREFIX": "lim",
        "TIMEOUT": 900,
        "OPTIONS": {
            "CLIENT_CLASS": "django_redis.client.DefaultClient",
            # لا IGNORE_EXCEPTIONS هنا عمدًا: مخزن الحدود يجب أن يصرخ
            # حين يتعثّر، لا أن يعود None فيقرأه الكود «صفر محاولات».
        },
    }
    RATELIMIT_USE_CACHE = "limits"
```

```bash
# deploy/hetzner/env.production
REDIS_LIMITS_URL=redis://:<REDIS_PASSWORD>@redis-limits:6379/0
```

ثم بدّل `cache` إلى `caches["limits"]` في `_login_throttle_key` وأخواتها وفي `SchoolRateLimitMiddleware`.

### 5.2 — مراقبة صحّة مخزن الحدود

المشروع يملك أصلًا `monitor-infrastructure-capacity` كل 5 دقائق مع `REDIS_MEMORY_ALERT_PERCENT=80` وتنبيهات Telegram. أضِف إليه العدّاد `auth.login.throttle_store_unavailable` كتنبيه فوري.

### 5.3 — TTL لمفاتيح إصدار اللوحة (P3-1 · SEC-011)

```python
# reports/cache_utils.py:91 و 109
# 30 يومًا بدل الدوام: المفتاح بلا TTL لا يُخلى تحت volatile-lru أبدًا،
# وتصفير الإصدار غير ضار — يُعاد إنشاؤه عند أول قراءة.
_DASHBOARD_VERSION_TTL = 30 * 24 * 3600
cache.add(key, 1, timeout=_DASHBOARD_VERSION_TTL)
```

**معيار الخروج:** `redis-limits` يعمل، حدود الدخول تصمد أمام إعادة تشغيل `redis` الرئيسي، والتنبيه يصل عند إسقاط `redis-limits` عمدًا.

---

## المرحلة 6 — Database & Performance

**الحالة:** 🟢 سليم — لا عمل حاجز. بنود ضبط دقيق فقط.

الفهارس المركّبة على `Report` و`NotificationRecipient` و`AuditLog` مشتقّة من أنماط الاستعلام الفعلية وموثَّقة بها. `unique_together` يمنع التكرار على مستوى القاعدة. `CONN_MAX_AGE=0` بتعليل صحيح تحت ASGI. مكافحة N+1 صريحة عبر `prefetch_memberships_for_school`. حماية Cache Stampede مكتملة.

### 6.1 — تمديد احتفاظ سجل التدقيق (P1-4 · SEC-010)

```bash
# deploy/hetzner/env.production
AUDIT_LOG_RETENTION_DAYS=365
```

مع أرشفة ما تجاوز 90 يومًا إلى R2 قبل الحذف. **راقب حجم `reports_auditlog`** بعد التغيير — إن تجاوز النمو المتوقع، فعّل تقسيم الجدول (partitioning) بالشهر.

### 6.2 — تفعيل PgBouncer عند العتبة

الـ profile جاهز في `compose.hetzner.yaml`. فعّله حين يقترب `WEB_CONCURRENCY × MAX_CONCURRENT_REQUESTS` من `max_connections`:

```bash
docker compose --profile pgbouncer up -d
# ثم وجّه DATABASE_URL إلى pgbouncer:6432 مع إبقاء CONN_MAX_AGE=0
```

### 6.3 — قياس فعلي قبل ادّعاء السعة

تحليل السعة في التقرير **نظري من الإعدادات** لغياب بيئة Staging. قبل الوعد بـ 1,000+ متزامنًا، شغّل `scripts/loadtest.py` على بيئة مطابقة — **لا على الإنتاج**.

---

## المرحلة 7 — Infrastructure

**الحالة:** 🟢 مصلَّبة فعليًا — بندان للإكمال.

`cap_drop: ALL`، `no-new-privileges:true`، `tmpfs` بـ `mode=1777`، شبكة `internal: true`، الويب مقيَّد بـ `127.0.0.1`، مستخدم غير جذري في `Dockerfile`، أسرار PostgreSQL و Redis في ملفات منفصلة عن أسرار التطبيق، نسخ احتياطي مجدول لـ PostgreSQL والوسائط مع `verify_restore.sh`. هذا مستوى تصلّب أعلى من المعتاد.

### 7.1 — إكمال ترويسات الحماية (P1-3 · SEC-006)

```
# deploy/hetzner/Caddyfile.fragment
# مصدر واحد للحقيقة. SecurityMiddleware في Django لا يكتب ترويسة موجودة
# أصلًا، فترك Caddy يكتب HSTS بصيغته يُلغي SECURE_HSTS_PRELOAD صامتًا.
header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
    Permissions-Policy "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    Cross-Origin-Resource-Policy "same-origin"
    -Server
}
```

### 7.2 — تثبيت شجرة الاعتماديات (P1-1 · SEC-004)

```bash
pip install pip-tools
pip-compile --generate-hashes --output-file=requirements.lock.txt requirements.txt
```

```dockerfile
# Dockerfile — استبدل السطر 42
COPY requirements.lock.txt /app/
RUN pip install --upgrade pip && \
    pip install --no-cache-dir --require-hashes -r requirements.lock.txt
```

```yaml
# .github/workflows/security.yml
name: security
on: [push, pull_request]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - run: docker build -t app-audit .
      # الفحص داخل الصورة المبنية — لا في بيئة المطوّر
      - run: docker run --rm app-audit sh -c "pip install pip-audit && pip-audit --strict"
      - run: docker run --rm app-audit sh -c "pip install bandit && bandit -r reports core config -ll"
```

**ترتيب الترقية — لا دفعة واحدة:**
1. `urllib3`, `requests`, `idna` — منخفضة الخطورة، عبر `boto3`. اختبر رفع/تنزيل ملف من R2.
2. `pygments`, `pyasn1`, `msgpack` — تبعية غير مباشرة، أثر محدود.
3. `twisted` — **منفصلًا وحده**. يمسّ `daphne`/WebSocket. اختبر اتصال WS والعدّادات اللحظية والانفصال بعد الخمول.

### 7.3 — تفعيل Sentry (P2-4)

```bash
# deploy/hetzner/env.production
SENTRY_DSN=<dsn>
SENTRY_TRACES_SAMPLE_RATE=0.05
```
`send_default_pii=False` مضبوط بالفعل في [config/settings.py:117](config/settings.py#L117) — لا يحتاج تغييرًا.

---

## المرحلة 8 — Hardening

**الحالة:** ⚪ تحسينات نهائية، بلا أثر على قرار الإطلاق.

### 8.1 — إزالة `cdn.jsdelivr.net` من `script-src` (P2-3 · SEC-007)
استضف الأصول محليًا (الأفضل) أو ثبّتها بـ SRI.

### 8.2 — حارس أسرار في pre-commit
```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: ["--baseline", ".secrets.baseline"]
```

### 8.3 — تنظيف مجلد العمل (P3-2)
احذف `db.sqlite3.backup-before-roles-migration` بعد تأكيد نجاح الترحيل.

### 8.4 — تنظيف تاريخ Git (P3-3)
`git filter-repo --path .env --invert-paths` — **يعيد كتابة كل الـ SHAs**، يحتاج تنسيقًا. التدوير في المرحلة الأولى يعالج الخطر الفعلي؛ هذا للنظافة.

### 8.5 — بيئة Staging (P3-4)
تتيح تنفيذ اختبار حمل حقيقي والتحقق الفعلي من أرقام السعة بدل الاعتماد على التحليل النظري.

---

## بروتوكول ما بعد كل إصلاح

يُنفَّذ **بعد كل مرحلة**، لا مرة واحدة في النهاية:

```bash
# 1) الاختبارات الوظيفية والأمنية كاملة
./.venv/Scripts/python.exe manage.py test reports core --settings=config.test_settings

# 2) التحليل الساكن
./.venv/Scripts/bandit.exe -r reports core config maintenance -ll -x "*/tests/*,*/migrations/*"
./.venv/Scripts/ruff.exe check reports core config

# 3) فحص النشر
ENV=production PRODUCTION_STRICT_MODE=0 DEBUG=0 SECRET_KEY=... \
  ./.venv/Scripts/python.exe manage.py check --deploy

# 4) فحص الاعتماديات
./.venv/Scripts/python.exe -m pip_audit -r requirements.txt --no-deps
```

### قائمة التحقق بعد كل مرحلة

- [ ] **No regression** — كل الاختبارات القائمة (109 ملفًا) تمرّ
- [ ] **No permission bypass** — `test_role_journeys.py`, `test_cross_school_role_routing.py`, `test_executive_director.py` تمرّ
- [ ] **No tenant leakage** — `test_tenant_isolation_matrix.py` (الجديد) يمرّ
- [ ] **No cache leakage** — مفاتيح اللوحة والعدّادات ما زالت تحمل `school_id`/`user_id`
- [ ] **No major performance degradation** — `test_reports_performance.py`, `test_scaling_protections.py`, `test_public_traffic_load.py` تمرّ
- [ ] **Deploy check clean** — لا تحذيرات جديدة من `check --deploy`

### تحقّق حيّ بعد النشر (Smoke Test)

```bash
# ترويسات الحماية
curl -sI -A "Mozilla/5.0" https://tawtheeq-ksa.com/ | \
  grep -iE "strict-transport|content-security|permissions-policy|cross-origin|x-frame"

# الصحة
curl -s https://tawtheeq-ksa.com/healthz/

# لا فهرسة للصفحات الخاصة
curl -sI -A "Mozilla/5.0" https://tawtheeq-ksa.com/login/ | grep -i x-robots-tag
```

---

## خريطة الطريق الزمنية

| المرحلة | الأولوية | الجهد | الحاجز للإطلاق؟ |
|---|---|---|---|
| 1 — Critical Security | — | صفر | لا يوجد ما يُنفَّذ |
| 2 — Tenant Isolation | P0 | 2 ساعة | ✅ نعم (2.1) |
| 3 — Auth & Authz | P0 | 3 ساعات | ✅ نعم (3.1، 3.2) |
| 4 — API Security | P2 | 3 ساعات | ❌ لا |
| 5 — Cache & Redis | P1 | 4 ساعات | ❌ لا (لكن يُوصى قبل الإطلاق مع 3.1) |
| 6 — Database & Perf | P1 | ساعة | ❌ لا |
| 7 — Infrastructure | P1 | 4 ساعات | ❌ لا |
| 8 — Hardening | P2/P3 | 4 ساعات | ❌ لا |

**المسار الحرج إلى 🟢 READY FOR PRODUCTION:**
المرحلة 2.1 + المرحلة 3.1 + المرحلة 3.2 + P0-1 (تدوير `SECRET_KEY`) ≈ **6 ساعات عمل**.

**توصية:** أنجِز المرحلة 5.1 (مثيل Redis للحدود) في نفس الدفعة. المرحلة 3.1 تجعل الدخول يفشل مغلقًا، وذلك يفترض مخزن حدود موثوقًا — تنفيذ الاثنين معًا يتجنّب مقايضة توفّر غير ضرورية.
