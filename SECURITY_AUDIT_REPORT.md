# تقرير الفحص الأمني والتقني الشامل — منصة توثيق

**النطاق:** `https://tawtheeq-ksa.com/` — مستودع `school_reports` (Django 5.2.16 / Python 3.12)
**تاريخ الفحص:** 2026-08-11
**فرع الفحص:** `main` @ `12ef4a41`
**المنهجية:** Static Code Analysis → Configuration Review → Dependency Audit → Read-only Dynamic Testing
**قواعد السلامة:** لم يُنفَّذ أي اختبار كتابي أو تدميري على الإنتاج. الفحص الحي اقتصر على قراءة ترويسات HTTP.

> **⚠️ حالة الوثيقة:** هذا التقرير يصف الحالة **قبل** جولة الإصلاح. نُفِّذت بعده 9 من 12 بندًا —
> راجع [CRITICAL_FIXES.md](CRITICAL_FIXES.md#حالة-التنفيذ) لجدول الحالة المحدَّث.
> الأوصاف والأدلة أدناه تُركت كما هي عمدًا: هي سجلّ ما وُجد، وأرقام الأسطر فيها تشير إلى الكود قبل التعديل.

---

## 1 — الملخص التنفيذي (Executive Summary)

منصة توثيق مبنية على معمارية Django أحادية مع فصل واضح للطبقات، وتُظهر **نضجًا هندسيًا وأمنيًا أعلى بكثير من المتوسط** لمنصات SaaS في هذه المرحلة. العزل متعدد المستأجرين — وهو أخطر ما في المنصة — **مطبَّق بشكل صحيح على ثلاث طبقات مستقلة**، لا على طبقة واحدة قابلة للنسيان.

**لم يُعثر على أي ثغرة Critical.** لم يُعثر على IDOR قابل للاستغلال في أيٍّ من 135 موضع جلب كائن فُحصت. لم يُعثر على SQL Injection أو Command Injection أو SSTI أو Path Traversal — المشروع خالٍ تمامًا من `raw()` و`cursor.execute` على مدخلات و`eval`/`exec`/`subprocess`.

المشكلات المكتشفة تتركز في **حِفظ الأسرار (Secrets Hygiene)** و**سلوك أنظمة الحماية عند تعثّر Redis (Fail-Open)** و**تصلّب البنية (Hardening)** — لا في منطق الصلاحيات ولا في عزل البيانات.

### القرار

# 🟡 READY AFTER FIXES

المنصة **صالحة للإطلاق التجاري بعد إصلاح 4 بنود (P0/P1) لا تتجاوز يومَي عمل**. لا يوجد سبب معماري أو منطقي يمنع الإطلاق؛ الأسباب المانعة حاليًا تشغيلية بحتة وقابلة للإصلاح دون لمس المعمارية.

### الدرجات

| المحور | الدرجة | التعليق |
|---|---|---|
| **Overall Security Score** | **86 / 100** | لا ثغرات Critical؛ خصم رئيسي على حفظ الأسرار وسلوك fail-open |
| **Isolation (العزل)** | **95 / 100** | ثلاث طبقات مستقلة؛ أقوى جانب في المنصة |
| **Authentication** | **90 / 100** | حدّان متكاملان (IP + حساب)، مقاومة timing، passkeys، جلسة واحدة |
| **Authorization** | **92 / 100** | الدور مُنطَق بالمدرسة لا عَلَم على الحساب؛ نطاق + تفويض مؤقت |
| **API Security** | **88 / 100** | DRF مقيّد افتراضيًا؛ كل ViewSet مُفلتر بالمستأجر |
| **Cache Security** | **90 / 100** | كل مفتاح يحمل `school_id` و/أو `user_id`؛ خصم على fail-open |
| **Infrastructure** | **88 / 100** | شبكة داخلية، `cap_drop: ALL`، لا منافذ مكشوفة، Cloudflare |
| **Database** | **90 / 100** | فهارس مركّبة مدروسة على الجداول الساخنة؛ PgBouncer جاهز |
| **Performance** | **87 / 100** | تفريغ PDF/ZIP للخلفية، مكافحة N+1 صريحة، stampede protection |
| **Resource Efficiency** | **82 / 100** | سقف تزامن + ميزانية لكل مستأجر؛ خصم على تعطّل الحدود عند سقوط Redis |

### إحصاءات الفحص

```
ملفات Python مفحوصة (بلا migrations/venv) : 296
أسطر كود مفحوصة بـ bandit                : 53,313
مواضع get_object_or_404 مراجَعة           : 135
ملفات اختبار موجودة في المشروع            : 109
Bandit — High severity                    : 0
Bandit — Medium severity                  : 11 (كلها False Positive أو مقبولة — راجَعناها يدويًا)
pip-audit على requirements.txt            : No known vulnerabilities
Django check --deploy                     : 1 warning فقط (مفتاح الفحص المؤقت، ليس مفتاح الإنتاج)
حقن SQL / أوامر / قوالب                   : 0
```

---

## 2 — خريطة المعمارية المكتشفة

| الطبقة | التقنية الفعلية |
|---|---|
| Application | Django 5.2.16، ASGI عبر `gunicorn` + `UvicornWorker` |
| Frontend | قوالب Django (SSR)، PWA مع Service Worker |
| Database | PostgreSQL 16 (`CONN_MAX_AGE=0` عمدًا تحت ASGI)، PgBouncer كـ profile اختياري |
| Cache | Redis 7.4 — DB 1، `KEY_PREFIX="sr"`، `django_redis` |
| Broker | نفس Redis — DB 0، Celery 5.4 بأربع طوابير (`default`/`notifications`/`images`/`periodic`) |
| Channels | نفس Redis — `asgi:*`، WebSocket للعدّادات اللحظية |
| Sessions | `cached_db` (قراءة من Redis، كتابة لـ Redis + PostgreSQL) |
| File Storage | Cloudflare R2 عبر `django-storages`، **خاص افتراضيًا بروابط موقَّعة** |
| Reverse Proxy | Caddy → خلف Cloudflare (challenge مفعّل) |
| Workers | `worker-core` (default+notifications)، `worker-media` (images+periodic)، `beat` |
| Auth Model | `AUTH_USER_MODEL = reports.Teacher` (مخصص) + WebAuthn/Passkeys |
| Tenant Model | `School` ← `SchoolMembership` ← `StaffScope`/`Delegation`؛ ومجموعات `SchoolGroup` فوقها |
| Payments | Moyasar (مفعّل)، Tamara (معطّل) — كلاهما hosted checkout بتحقق من جهة الخادم |
| Monitoring | Sentry (اختياري)، `opmetrics`، تنبيهات Telegram، `/ops/metrics/` (superuser فقط) |

---

## 3 — فحص العزل متعدد المستأجرين (Multi-Tenant Isolation)

**هذا أهم محور في الفحص، وهو أقوى ما في المنصة.**

العزل ليس معتمدًا على انضباط المطور في كل عرض، بل مفروض على **ثلاث طبقات مستقلة** تفشل كل منها مغلقة:

### الطبقة 1 — Middleware يتحقق من العضوية قبل أي عرض

[reports/middleware.py:434-561](reports/middleware.py#L434-L561) — `ActiveSchoolGuardMiddleware`

```python
membership = SchoolMembership.objects.filter(
    teacher=user, school_id=sid, is_active=True,
).select_related("school").first()
if membership is None:
    if self._wants_json(request):
        return JsonResponse({"detail": "forbidden"}, status=403)
    request.session.pop(self.SESSION_KEY, None)
```

قيمة `active_school_id` في الجلسة **لا تُصدَّق أبدًا**. تُتحقَّق مقابل عضوية سارية في كل طلب، وتُمسح فورًا إن لم تُثبَت. تزوير `session.active_school_id` — حتى لو تمكّن المهاجم من ذلك — لا يمنح شيئًا.

### الطبقة 2 — دوال الصلاحية تسأل دائمًا «داخل مدرسة بعينها»

[reports/permissions.py:291-313](reports/permissions.py#L291-L313) — `_has_school_role`

```python
school_id = _resolved_school_id(...)
if not school_id:
    # بلا مدرسة لا معنى للسؤال. لا نتوسّع إلى «أي مدرسة»
    return False
```

الدور ليس عَلَمًا على الحساب بل عضوية مُنطَقة. من كان وكيلًا في مدرسة لا يصير وكيلًا في أخرى بتبديل المدرسة النشطة. الكود يوثّق صراحةً أن هذا درس من إخفاق سابق في المشروع (`docs/REMOVE_SUPERVISOR_ROLES.md`).

### الطبقة 3 — فلتر المستأجر مطويّ داخل دالة الاستعلام نفسها

[reports/permissions.py:1302-1345](reports/permissions.py#L1302-L1345) — `restrict_queryset_for_user`

```python
qs = _scope_to_school(qs, active_school)   # ← الفلتر مطبَّق هنا، لا عند المستدعي
```

الكود يشرح السبب بدقة: أكواد أنواع التقارير مخصَّصة لكل مدرسة **وقد تتكرر** بينها، فالفلترة بـ `category__code` وحدها غير معزولة. طيّ الفلتر إلى الداخل يجعل الدالة آمنة وحدها ولا يعتمد على انضباط المستدعي القادم.

### فحص فشل السياق (Fail-Closed)

[reports/services_reports.py:206-231](reports/services_reports.py#L206-L231)

```python
# غير المالك: لا تقرير خارج مدرسة نشطة محدَّدة.
if active_school is None:
    return get_object_or_404(qs, pk=pk, teacher=user)
```

غياب السياق **يضيّق** النطاق ولا يوسّعه. هذا بالضبط عكس النمط الخاطئ الشائع. والتعليق يوثّق أن الشرط كان `is_staff` سابقًا وأن ذلك كان يعيد أي تقرير في المنصة برقمه — أي أن الفريق اكتشف هذه الثغرة وأصلحها.

### فحص IDOR — 135 موضع

| الملف:السطر | النمط | الحكم |
|---|---|---|
| [reports/views/notifications.py:120](reports/views/notifications.py#L120), [217](reports/views/notifications.py#L217), [461](reports/views/notifications.py#L461), [542](reports/views/notifications.py#L542) | جلب ثم فحص `n.school_id != active_school.id` | ✅ آمن |
| [reports/views/tickets.py:393](reports/views/tickets.py#L393) | جلب ثم `_assert_ticket_access` (حدّ المستأجر + الطرفية) | ✅ آمن |
| [reports/views/documents.py:133](reports/views/documents.py#L133) | فحص مزدوج: `school_id` + `visible_documents(...)` | ✅ آمن |
| [reports/views/reports.py:1338](reports/views/reports.py#L1338), [1694](reports/views/reports.py#L1694) | `qs` مفلتر بـ `school=active_school` قبل الجلب | ✅ آمن |
| [reports/views/group_oversight.py:88](reports/views/group_oversight.py#L88) | `get_object_or_404(executive_director_schools_qs(user), pk=pk)` | ✅ آمن |
| [reports/views/reporttypes.py:138](reports/views/reporttypes.py#L138) | فرع superuser فقط؛ غيره `school=active_school` | ✅ آمن |
| [reports/views/customer_care.py:84](reports/views/customer_care.py#L84) | `@_superuser_required` | ✅ آمن |
| [reports/views/subscriptions.py:2059](reports/views/subscriptions.py#L2059) | `@user_passes_test(is_superuser)` | ✅ آمن |

**النتيجة: لم يُعثر على IDOR ولا Broken Object Level Authorization قابل للاستغلال.**

### فحص عزل الكاش

[reports/cache_utils.py](reports/cache_utils.py) — **كل مفتاح يحمل هوية المستأجر أو المستخدم:**

```python
def key_school_stats(school_id)  -> "school_stats:{school_id}"
def key_unread_count(user_id)    -> "unread:{user_id}"
_dashboard_payload_key(...)      -> "school_dashboard:payload:{school_id}:{period}:{version}"
f"unreadcnt:v1:u{uid}:s{sid}"
f"ws:counts:{uid}:{sid}"
f"school-rate:v1:{school.pk}:{bucket}"
```

**لا يوجد مفتاح واحد بصيغة `dashboard` أو `reports` أو `home` مجرَّدًا.** سيناريو «المستخدم A يفتح لوحة، ثم B يفتح نفس المسار فيرى بيانات A» **غير ممكن**: مفتاح اللوحة يحمل `school_id`، ومفتاح العدّادات يحمل `user_id` + `school_id`.

كما أن `/` يُرسَل بـ `Cache-Control: no-cache, no-store, must-revalidate, private` و`vary: Cookie` (مُتحقَّق منه حيًّا)، فلا تخزّن Cloudflare استجابة مستخدم لآخر.

### فحص عزل WebSocket

[reports/consumers.py:137](reports/consumers.py#L137) — المجموعة هي `notif.u{user_id}` — **لكل مستخدم مجموعته**، لا مجموعة لكل مدرسة يمكن أن يدخلها غريب.

[reports/consumers.py:426-442](reports/consumers.py#L426-L442) — `_resolve_allowed_school_id` يرفض أي `active_school_id` يرسله العميل ما لم يطابق ما في الجلسة، ويسقط إلى قيمة الجلسة عند الاختلاف.

### فحص عزل الملفات

- الأسماء عشوائية: [reports/model_parts/base.py:64-70](reports/model_parts/base.py#L64-L70) — `secrets.token_hex(8)` = **64 بت من العشوائية** لكل ملف. التخمين غير عملي.
- التخزين خاص إجباريًا: [config/settings.py:1171-1173](config/settings.py#L1171-L1173) — `MEDIA_PUBLIC_ACCESS_ENABLED` يرفع `ImproperlyConfigured` إن فُعّل في الإنتاج الصارم.
- الروابط موقَّعة إجباريًا: [config/settings.py:36-42](config/settings.py#L36-L42) — `_media_querystring_auth_enabled` يتجاهل أي `AWS_QUERYSTRING_AUTH=0` قديم ما دام الوصول العام معطّلًا.

---

## 4 — سِجل النتائج (Findings)

---

### SEC-001 — أسرار إنتاج حقيقية محفوظة في تاريخ Git

| | |
|---|---|
| **Severity** | 🟠 **High** |
| **Affected File** | `.env` (محذوف من التتبع، باقٍ في التاريخ) |
| **Affected Commits** | 19 commit، منها `8785b272`, `3db44c39`, `9c9fb690`, `d0d15ae5` |
| **Affected Endpoint** | — (Repository) |
| **CWE** | CWE-540 (Inclusion of Sensitive Information in Source Code) |

> ### ✅ تُحقِّق على الإنتاج (2026-08-11) — الخطر **غير قائم**
>
> قُورنت أسرار الإنتاج الحيّة ببصمات القيم المسرَّبة مباشرةً على الخادم:
>
> ```
> SECRET_KEY   length=88  distinct=50  IS_LEAKED = False
> DATABASE_URL                         IS_LEAKED = False
> DATABASE_URL host = postgresql://***@postgres:5432/school_reports
> ```
>
> **المفتاح الإنتاجي الجاري ليس أيًّا من المفاتيح الأربعة المسرَّبة** — دُوِّر في وقت سابق. وسلسلة قاعدة البيانات تشير إلى `postgres` داخل شبكة Docker الداخلية، لا إلى مثيل Render المسرَّب. **فلا تدوير مطلوبًا، ولا إسقاط للجلسات.**
>
> يبقى المطلوب: حذف مفاتيح Cloudinary من لوحتها (لم تعد مستخدمة)، والتأكد من تفكيك قاعدة بيانات Render القديمة.
>
> الوصف أدناه يبقى سجلًّا لما وُجد في التاريخ، والحارس الآلي ([core/compromised_secrets.py](core/compromised_secrets.py)) يبقى قائمًا لمنع الانحدار.

**Description**
ملف `.env` كان مُتتبَّعًا في Git عبر 19 commit قبل إزالته في `8c9fed3a`. الإزالة من التتبع **لا تحذف المحتوى من التاريخ** — كل نسخة سابقة ما زالت قابلة للاستخراج بأمر واحد.

**Evidence**

```
$ git log --all --oneline -- .env | wc -l
19

$ git show 8785b272:.env   # الأسماء فقط، القيم محجوبة في هذا التقرير
ENV           = produc…
DEBUG         = False…
SECRET_KEY    = $!$0hw…      ← مفتاح توقيع إنتاج
DATABASE_URL  = postgr…      ← سلسلة اتصال بقاعدة بيانات مع بيانات اعتماد
CLOUDINARY_API_KEY    = 469557…
CLOUDINARY_API_SECRET = aJXnXw…   ← مفتاح تخزين حي
ALLOWED_HOSTS = school…
```

**Attack Scenario**
مَن يملك وصولًا للمستودع (متعاون حالي أو سابق، توكن CI مسرَّب، نسخة مستنسخة على جهاز، أو تحويل المستودع إلى Public بالخطأ) يستخرج `SECRET_KEY` الإنتاجي بأمر `git show`. وبمفتاح توقيع Django يمكنه تزوير كوكيز الجلسة، وتزوير توكنات إعادة تعيين كلمة المرور، وفكّ أي بيانات موقَّعة — **أي انتحال هوية أي مستخدم بما فيهم مالك النظام، دون كلمة مرور.**

**Impact**
- انتحال كامل للهوية عبر تزوير الجلسات (لو كان المفتاح ما زال قيد الاستخدام).
- وصول مباشر لقاعدة البيانات التاريخية (لو كانت ما زالت حيّة).
- تحكّم في حساب Cloudinary التاريخي.

**عوامل التخفيف المؤكَّدة (تُخفّض التصنيف من Critical إلى High):**
1. **المستودع خاص** — تحقّقناه: `gh repo view --json visibility` → `PRIVATE`.
2. **`.env` الحالي محلي وتطويري فقط** — `SECRET_KEY=dev-unsafe…`، `MOYASAR_ENVIRONMENT=test` (`sk_test_…`)، `ENV=development`. **لا يوجد سرّ إنتاج في نسخة العمل الحالية.**
3. أسرار الإنتاج تُولَّد على الخادم في `deploy/hetzner/env.production` ولم تدخل Git قط.
4. Cloudinary لم يعد مستخدمًا (المنصة انتقلت إلى Cloudflare R2).

**Recommended Fix**
1. **تحقّق أولًا:** هل `SECRET_KEY` الحالي في `deploy/hetzner/env.production` مساوٍ لـ `$!$0hw…`؟ إن كان — **دوِّره فورًا**.
2. دوّر بيانات اعتماد قاعدة البيانات التاريخية، أو أكّد أن المثيل مُفكَّك.
3. احذف/دوّر مفاتيح Cloudinary من لوحة تحكمها.
4. أضف حارسًا يمنع التكرار.

**Example Fix**

```bash
# 1) توليد مفتاح جديد وتدويره على الخادم
python -c "import secrets; print(secrets.token_urlsafe(64))"
# ثم حدّث SECRET_KEY في deploy/hetzner/env.production وأعد تشغيل الحاويات.
# ملاحظة تشغيلية: تدوير المفتاح يُسقط كل الجلسات النشطة (سلوك مقبول ومقصود).

# 2) حارس pre-commit يمنع عودة .env
cat >> .pre-commit-config.yaml <<'YAML'
repos:
  - repo: https://github.com/Yelp/detect-secrets
    rev: v1.5.0
    hooks:
      - id: detect-secrets
        args: ["--baseline", ".secrets.baseline"]
YAML

# 3) (اختياري، منسَّق) تنظيف التاريخ — يعيد كتابة كل الـ SHAs
#    git filter-repo --path .env --invert-paths && git push --force --all
```

**Regression Test**

```python
# reports/tests/test_secrets_hygiene.py
import subprocess
from django.test import SimpleTestCase


class SecretsHygieneTests(SimpleTestCase):
    def test_env_file_is_not_tracked_by_git(self):
        """‏.env يجب ألا يعود إلى التتبع أبدًا."""
        tracked = subprocess.run(
            ["git", "ls-files", ".env", "deploy/hetzner/env.production"],
            capture_output=True, text=True,
        ).stdout.strip()
        self.assertEqual(tracked, "", f"ملفات أسرار عادت إلى تتبع Git: {tracked}")

    def test_production_secret_key_is_not_a_known_leaked_value(self):
        """المفتاح الإنتاجي يجب ألا يطابق أي مفتاح ظهر في تاريخ Git."""
        from django.conf import settings
        import hashlib
        LEAKED_SHA256_PREFIXES = {
            # ضع هنا sha256(المفتاح المسرَّب)[:16] بعد استخراجه — لا المفتاح نفسه
        }
        digest = hashlib.sha256(settings.SECRET_KEY.encode()).hexdigest()[:16]
        self.assertNotIn(digest, LEAKED_SHA256_PREFIXES)
```

---

### SEC-002 — ضوابط الحماية تفشل مفتوحة (Fail-Open) عند تعثّر Redis

| | |
|---|---|
| **Severity** | 🟠 **High** |
| **Affected File** | [config/settings.py:664](config/settings.py#L664), [reports/views/auth.py:504-534](reports/views/auth.py#L504-L534) |
| **Affected Function** | `_login_account_locked`, `_register_login_failure`, `_daily_budget_exhausted`, `SchoolRateLimitMiddleware` |
| **Affected Endpoint** | `/login/`, `/platform-login/`, `/mansour/reply/`, وكل نقطة عليها `@ratelimit` |
| **CWE** | CWE-703 (Improper Check for Unusual Conditions) |

**Description**
الكاش مُعدّ بـ `"IGNORE_EXCEPTIONS": True`، وكل ضوابط الحماية القائمة على العدّادات تعالج فشل الكاش بـ «اسمح»:

```python
# config/settings.py:664
"OPTIONS": {"CLIENT_CLASS": "...DefaultClient", "IGNORE_EXCEPTIONS": True},

# reports/views/auth.py:514-517
try:
    return int(cache.get(_login_throttle_key(identifier)) or 0) >= LOGIN_ACCOUNT_MAX_FAILURES
except Exception:
    return False          # ← «غير مقفل» عند فشل الكاش
```

ومع سياسة Redis `volatile-lru` ([compose.hetzner.yaml](compose.hetzner.yaml)) فإن ضغط الذاكرة **يُخلي مفاتيح العدّادات صامتًا** لأنها الوحيدة الحاملة لـ TTL. لا استثناء يُرفع، ولا سجلّ يُكتب — العدّاد ببساطة يعود صفرًا.

**Attack Scenario**
المهاجم يولّد ضغط ذاكرة على Redis (أو ينتظر ذروة طبيعية / انقطاعًا)، فتُخلى مفاتيح `login:fail:*` باستمرار. عندئذٍ:
- حدّ الحساب (8 محاولات / 15 دقيقة) **يُصفَّر مع كل إخلاء** → تخمين كلمات المرور بلا سقف فعلي.
- حدود `django-ratelimit` على كل نقطة (`@ratelimit`) **تختفي كليًّا** — وهي تقرأ من نفس الكاش.
- `SchoolRateLimitMiddleware` يعود `self.get_response(request)` عند أي استثناء → ميزانية المستأجر تختفي.
- `_daily_budget_exhausted` للمساعد الذكي يعود `False` → **سقف الفاتورة اليومية يختفي**.

الأخطر: هذا يحدث **بالضبط وقت الضغط**، أي حين تكون الحماية أشدّ لزومًا.

**Impact**
اختفاء متزامن لكل طبقات مكافحة الإساءة: Brute Force، Credential Stuffing، استنزاف الموارد، وتضخّم فاتورة الـ AI — دون أي إشارة في السجلّات.

**Evidence**
`cache.get`/`cache.incr` تُستعمل كمصدر وحيد للحقيقة في: `auth.py:514`, `auth.py:522`, `core/middleware.py:233`, `core/middleware.py:288`, `consumers.py:92`, `consumers.py:111`, وكل `@ratelimit` (31 موضعًا).

**Recommended Fix**
1. **إقفال الدخول يفشل مغلقًا** — تعذّر التحقق من العدّاد يجب أن يمنع لا أن يسمح.
2. **افصل مفاتيح الحدود عن الكاش القابل للإخلاء** — Redis DB مستقل بسياسة `noeviction`، أو بادئة محميّة.
3. **نبّه عند الفشل** — لا تبتلع استثناء الكاش صامتًا في مسار أمني.

**Example Fix**

```python
# reports/views/auth.py
_LOGIN_THROTTLE_FAIL_CLOSED = True   # قابل للضبط عبر settings

def _login_account_locked(identifier: str) -> bool:
    if not identifier:
        return False
    try:
        return int(cache.get(_login_throttle_key(identifier)) or 0) >= LOGIN_ACCOUNT_MAX_FAILURES
    except Exception:
        # لا يمكن التحقق ⇒ لا نمنح المهاجم نافذة مفتوحة.
        logger.error("Login throttle store unavailable — failing closed", exc_info=True)
        opmetrics.increment("auth.login.throttle_store_unavailable")
        return bool(_LOGIN_THROTTLE_FAIL_CLOSED)
```

```yaml
# compose.hetzner.yaml — مثيل Redis ثانٍ للحدود، لا يُخلي شيئًا
  redis-limits:
    image: redis:7.4-alpine
    restart: unless-stopped
    security_opt: [no-new-privileges:true]
    env_file: [ "${REDIS_ENV_FILE:-deploy/hetzner/env.redis}" ]
    mem_limit: 128m
    command: >-
      sh -c 'exec redis-server --maxmemory 96mb
      --maxmemory-policy noeviction
      --requirepass "$${REDIS_PASSWORD}"'
    networks: [backend]
```

**Regression Test**

```python
# reports/tests/test_throttle_fail_closed.py
from unittest.mock import patch
from django.test import TestCase, Client
from django.urls import reverse


class ThrottleFailClosedTests(TestCase):
    def test_login_is_blocked_when_cache_backend_is_down(self):
        """سقوط Redis يجب أن يمنع الدخول لا أن يفتحه."""
        with patch("reports.views.auth.cache.get", side_effect=ConnectionError("redis down")):
            response = Client().post(
                reverse("reports:login"),
                {"phone": "0500000000", "password": "whatever"},
            )
        self.assertNotEqual(response.status_code, 200,
                            "الدخول نجح رغم تعذّر التحقق من عدّاد المحاولات")
```

---

### SEC-003 — تسجيل بيانات شخصية (رقم الجوال / الهوية) في سجلات التطبيق

| | |
|---|---|
| **Severity** | 🟡 **Medium** |
| **Affected File** | [reports/views/auth.py:816](reports/views/auth.py#L816), [820](reports/views/auth.py#L820), [824](reports/views/auth.py#L824) |
| **Affected Function** | `login_view` |
| **Affected Endpoint** | `POST /login/`, `POST /platform-login/` |
| **CWE** | CWE-532 (Insertion of Sensitive Information into Log File) |

**Description**
عند فشل الدخول يُسجَّل المعرِّف الخام — وهو **رقم جوال أو رقم هوية وطنية**:

```python
# auth.py:820
logger.warning("Login failed invalid-credentials identifier=%s trace_id=%s",
               identifier, getattr(request, "trace_id", None))
```

وهو تناقض داخلي في نفس الملف: [auth.py:530](reports/views/auth.py#L530) يفعل الصواب ويسجّل تجزئة فقط، مع تعليق يشرح تحديدًا أن المعرِّف «بيانات شخصية لا توضع مفتاحًا في Redis مشترك».

**Attack Scenario**
المهاجم يرسل قائمة أرقام هوية إلى `/login/` بكلمات مرور خاطئة. كل محاولة تكتب رقم الهوية في `stdout` للحاوية → `docker logs` → أي نظام تجميع سجلات (وSentry إن فُعّل). النتيجة: **قائمة بأرقام هويات وجوالات مواطنين مخزَّنة خارج قاعدة البيانات وخارج ضوابطها**، متاحة لكل من يملك وصولًا للسجلات — وهم عادةً أوسع دائرة من مَن يملك وصولًا لقاعدة البيانات.

**Impact**
انتهاك مبدأ تقليل البيانات في نظام حماية البيانات الشخصية السعودي (PDPL). تسريب PII عبر قناة جانبية دون اختراق.

**Recommended Fix**
استعمل نفس التجزئة الموجودة أصلًا في الملف.

**Example Fix**

```python
# reports/views/auth.py — قبل login_view
def _identifier_for_log(identifier: str) -> str:
    """معرِّف قابل للربط في التحقيقات، غير قابل للعكس إلى PII."""
    return _login_throttle_key(identifier)[-12:] if identifier else "-"


# ثم في المواضع الثلاثة:
logger.warning(
    "Login failed invalid-credentials identifier_hash=%s trace_id=%s",
    _identifier_for_log(identifier),
    getattr(request, "trace_id", None),
)
```

**Regression Test**

```python
# reports/tests/test_pii_not_logged.py
from django.test import TestCase, Client
from django.urls import reverse


class PiiLoggingTests(TestCase):
    def test_failed_login_does_not_log_raw_identifier(self):
        national_id = "1098765432"
        with self.assertLogs("reports.views.auth", level="WARNING") as captured:
            Client().post(reverse("reports:login"),
                          {"phone": national_id, "password": "wrong-password"})
        joined = "\n".join(captured.output)
        self.assertNotIn(national_id, joined,
                         "رقم الهوية ظهر خامًا في سجلات التطبيق")
        self.assertIn("identifier_hash=", joined)
```

---

### SEC-004 — الاعتماديات غير المباشرة غير مثبَّتة؛ صورة الإنتاج تحمل حزمًا بثغرات معروفة

| | |
|---|---|
| **Severity** | 🟡 **Medium** |
| **Affected File** | [requirements.txt](requirements.txt), [Dockerfile:42](Dockerfile#L42) |
| **CWE** | CWE-1104 (Use of Unmaintained Third Party Components) |

**Description**
`requirements.txt` يثبّت الاعتماديات **المباشرة** فقط. `pip install -r requirements.txt` في `Dockerfile:42` يحلّ الاعتماديات غير المباشرة وقت البناء — أي أن **صورتين مبنيتين في يومين مختلفين تحملان شجرتَي اعتماديات مختلفتين**، ولا سجلّ لما دخل الإنتاج فعلًا.

**Evidence**

```
$ pip-audit -r requirements.txt --no-deps
No known vulnerabilities found          ← المباشرة نظيفة ✅

$ pip-audit                             ← البيئة المحلولة فعليًا
urllib3   2.6.2   PYSEC-2026-141, PYSEC-2026-142, PYSEC-2026-1996   → 2.7.0
requests  2.32.5  PYSEC-2026-2275                                   → 2.33.0
twisted   25.5.0  PYSEC-2026-160                                    → 26.4.0
idna      3.11    PYSEC-2026-215                                    → 3.15
msgpack   1.1.2   PYSEC-2026-3625                                   → 1.2.1
pyasn1    0.6.3   PYSEC-2026-3455/3456/3457                         → 0.6.4
pygments  2.19.2  PYSEC-2026-2987                                   → 2.20.0
```

`urllib3`/`requests` تصلان عبر `boto3` (R2)، و`twisted` عبر `daphne`، و`msgpack`/`pyasn1` عبر `channels-redis`/`celery`. **كلها مسارات إنتاج فعلية.**

**تحفّظ منهجي مهم:** الجدول أعلاه من البيئة المحلية (`.venv`)، وليست بالضرورة مطابقة لصورة الإنتاج. **الرقم الصحيح يُستخرج بتشغيل `pip-audit` داخل الصورة المبنية** — وهذا جزء من الإصلاح المقترح.

**Attack Scenario**
ثغرات `urllib3` تاريخيًا تشمل تسريب ترويسات عبر إعادة التوجيه وتلويث طلبات. المسار الواقعي هنا: `boto3` → R2، وهو مسار يحمل توقيعات AWS. الاستغلال يتطلب مُدخلًا يتحكم به المهاجم في وجهة الطلب — غير متاح حاليًا في هذه القاعدة — فالخطورة الحالية **نظرية لا عملية**، لكن غياب التثبيت يعني أنك لا تعرف ما لديك في المرة القادمة.

**Recommended Fix**
ثبّت الشجرة كاملة بالتجزئات، واجعل `pip-audit` بوّابة في CI تعمل على الصورة المبنية.

**Example Fix**

```bash
# 1) توليد قفل كامل بالتجزئات
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
      # الفحص داخل الصورة الفعلية، لا في بيئة المطوّر
      - run: docker run --rm app-audit sh -c "pip install pip-audit && pip-audit --strict"
      - run: docker run --rm app-audit sh -c "pip install bandit && bandit -r reports core config -ll"
```

**Regression Test**
بوّابة CI أعلاه هي الاختبار — فشل البناء عند ظهور CVE جديد.

---

### SEC-005 — صلاحية الروابط الموقَّعة للوسائط 24 ساعة

| | |
|---|---|
| **Severity** | 🟡 **Medium** |
| **Affected File** | [config/settings.py:1208](config/settings.py#L1208) |
| **Affected Endpoint** | كل روابط R2 الموقَّعة (تقارير، إيصالات دفع، شواهد الإنجاز، مرفقات التعاميم) |
| **CWE** | CWE-613 (Insufficient Session Expiration) |

**Description**

```python
AWS_QUERYSTRING_EXPIRE = int(os.getenv("AWS_QUERYSTRING_EXPIRE", "86400"))  # 24 ساعة
```

الرابط الموقَّع **يحمل التخويل في ذاته** — من يحصل عليه يفتح الملف بلا حساب ولا جلسة ولا عضوية. عمرٌ 24 ساعة يعني نافذة يوم كامل بعد أي تسريب عرضي.

**Attack Scenario**
مدير مدرسة يفتح إيصال دفع أو مستند حساس. الرابط الموقَّع يصل إلى: سجل المتصفح، ذاكرة الجهاز المشترك، لقطة شاشة تُشارك في مجموعة واتساب، أو ترويسة `Referer` إن حُمّل مورد خارجي من صفحة العرض. أي شخص يلتقط الرابط خلال 24 ساعة **يقرأ مستند مدرسة ليس عضوًا فيها**.

`Referrer-Policy: strict-origin-when-cross-origin` (مؤكَّد حيًّا) يقلّص تسريب الـ Referer، لكنه لا يعالج المسارات الأخرى.

**Impact**
تجاوز عزل المستأجرين عبر تسريب رابط — وهو المسار الوحيد المتبقي للوصول إلى ملفات مدرسة أخرى.

**Recommended Fix**
قصّر العمر إلى دقائق. الروابط تُولَّد عند العرض، فلا حاجة لصلاحية ممتدة.

**Example Fix**

```python
# config/settings.py:1208
# عمر الرابط الموقَّع = مدة الاستهلاك المتوقعة لا مدة الجلسة. الرابط يحمل
# التخويل في ذاته، فطول عمره هو بالضبط طول نافذة التسريب.
AWS_QUERYSTRING_EXPIRE = int(os.getenv("AWS_QUERYSTRING_EXPIRE", "900"))  # 15 دقيقة
```

```bash
# deploy/hetzner/env.production
AWS_QUERYSTRING_EXPIRE=900
```

**ملاحظة توافقية:** أي رابط مُضمَّن في بريد أو مُخزَّن في قاعدة البيانات سينتهي. الفحص يُظهر أن الروابط تُولَّد وقت العرض من `FileField.url`، فالأثر متوقَّع أن يكون صفرًا — لكن **اختبر تنزيل أرشيف ZIP وإيصالات الدفع قبل النشر.**

**Regression Test**

```python
# reports/tests/test_media_url_expiry.py
from django.test import SimpleTestCase, override_settings
from django.conf import settings


class MediaUrlExpiryTests(SimpleTestCase):
    def test_signed_media_urls_expire_within_one_hour(self):
        """الرابط الموقَّع يحمل التخويل بذاته — فلا يعيش ساعات."""
        self.assertLessEqual(
            int(getattr(settings, "AWS_QUERYSTRING_EXPIRE", 86400)), 3600,
            "روابط الوسائط الموقَّعة تعيش أكثر من ساعة",
        )

    def test_public_media_access_stays_disabled(self):
        self.assertFalse(getattr(settings, "MEDIA_PUBLIC_ACCESS_ENABLED", False))
```

---

### SEC-006 — ترويسات حماية ناقصة على استجابات التطبيق

| | |
|---|---|
| **Severity** | 🔵 **Low** |
| **Affected File** | [reports/middleware.py:751-934](reports/middleware.py#L751-L934), `deploy/hetzner/Caddyfile.fragment` |
| **Affected Endpoint** | كل المسارات |
| **CWE** | CWE-693 (Protection Mechanism Failure) |

**Description**
الترويسات المرصودة حيًّا على `https://tawtheeq-ksa.com/` (بـ User-Agent متصفح، متجاوزًا صفحة تحدّي Cloudflare):

| الترويسة | الحالة |
|---|---|
| `content-security-policy` | ✅ قوية — `frame-ancestors 'none'`, `object-src 'none'`, nonce |
| `strict-transport-security` | ⚠️ `max-age=31536000; includeSubDomains` — **بلا `preload`** |
| `x-content-type-options` | ✅ `nosniff` |
| `x-frame-options` | ✅ `DENY` |
| `referrer-policy` | ✅ `strict-origin-when-cross-origin` |
| `cross-origin-opener-policy` | ✅ `same-origin` |
| `Permissions-Policy` | ❌ **غائبة** |
| `Cross-Origin-Resource-Policy` | ❌ **غائبة** |

**السبب الجذري للـ HSTS:** الإعداد `SECURE_HSTS_PRELOAD = True` موجود في [config/settings.py:1239](config/settings.py#L1239)، لكن `SecurityMiddleware` في Django **لا يكتب الترويسة إن كانت موجودة أصلًا في الاستجابة**. الترويسة الحية تظهر مع `via: 1.1 Caddy` — أي أن Caddy يضعها أولًا بصيغته الخاصة، فتُتجاهل صيغة Django بالكامل. **إعداد صحيح في الكود لا أثر له في الإنتاج** — وهذا أسوأ من غيابه لأنه يبدو كأنه يعمل.

**Impact**
- بلا `preload`: أول زيارة على شبكة معادية قابلة لهجوم SSL-strip قبل تثبيت HSTS.
- بلا `Permissions-Policy`: أي إطار/سكربت يعمل في الصفحة يستطيع طلب الكاميرا/الموقع/الميكروفون.
- بلا `CORP`: موارد التطبيق قابلة للتضمين من أصول أخرى.

**Recommended Fix**

```
# deploy/hetzner/Caddyfile.fragment
# مصدر واحد للحقيقة: إمّا Caddy أو Django، لا الاثنان. هنا Caddy يكمّل ولا يعارض.
header {
    Strict-Transport-Security "max-age=31536000; includeSubDomains; preload"
    Permissions-Policy "accelerometer=(), camera=(), geolocation=(), gyroscope=(), magnetometer=(), microphone=(), payment=(), usb=()"
    Cross-Origin-Resource-Policy "same-origin"
    -Server
}
```

**Regression Test**

```python
# reports/tests/test_security_headers.py
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(ENV="production", SECURE_HSTS_PRELOAD=True, CSP_ENABLED=True)
class SecurityHeaderTests(TestCase):
    def test_landing_page_carries_the_baseline_headers(self):
        response = self.client.get(reverse("reports:landing"), secure=True)
        csp = response.headers.get("Content-Security-Policy", "")
        self.assertIn("frame-ancestors 'none'", csp)
        self.assertIn("object-src 'none'", csp)
        self.assertEqual(response.headers.get("X-Frame-Options"), "DENY")
        self.assertEqual(response.headers.get("X-Content-Type-Options"), "nosniff")
```

> اختبار `preload` نفسه لا يمكن أن يقع في Django لأن Caddy يكتبه — أضِفه إلى فحص ما بعد النشر (smoke test) بـ `curl -I`.

---

### SEC-007 — `script-src` يسمح بأصل CDN عام كاملًا

| | |
|---|---|
| **Severity** | 🔵 **Low** |
| **Affected File** | [reports/middleware.py:872-873](reports/middleware.py#L872-L873) |
| **CWE** | CWE-1021 |

**Description**

```python
f"script-src 'self' 'nonce-{nonce}' https://cdn.jsdelivr.net{seal_script_source}",
```

`cdn.jsdelivr.net` يخدم **كل حزمة npm وكل مستودع GitHub**. إدراجه كأصل مسموح يعني أن CSP لم تعد تحصر السكربتات في ما تملكه المنصة: أي بدائية حقن HTML تتيح `<script src="https://cdn.jsdelivr.net/npm/<أي-حزمة>">` وتتخطى الـ nonce.

الأثر العملي محدود لأن الحقن نفسه غير موجود (المشروع يعتمد الهروب التلقائي في القوالب و`mark_safe` واحدة فقط على محتوى مملوك للمستودع — راجَعناها في [reports/templatetags/maintenance_text.py:73](reports/templatetags/maintenance_text.py#L73) وهي آمنة).

**Recommended Fix**
استضف الأصول محليًا (الأفضل — يزيل الاعتماد الخارجي ويحسّن الأداء)، أو ثبّتها بـ SRI.

**Example Fix**

```html
<!-- بديل SRI إن تعذّرت الاستضافة المحلية -->
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"
        integrity="sha384-<hash>" crossorigin="anonymous" nonce="{{ request.csp_nonce }}"></script>
```

---

### SEC-008 — `moyasar_callback` غير مصادَق ويسمح بتحفيز مزامنة لأي مرجع دفعة

| | |
|---|---|
| **Severity** | 🔵 **Low** |
| **Affected File** | [reports/views/subscriptions.py:3317-3327](reports/views/subscriptions.py#L3317-L3327) |
| **Affected Endpoint** | `POST /payments/moyasar/callback/<batch_ref>/` |
| **CWE** | CWE-306 |

**Description**
النقطة عامة بلا توكن. **التصميم صحيح من ناحية سلامة البيانات**: الحالة لا تُقرأ من جسم الطلب إطلاقًا بل يعاد اشتقاقها من واجهة Moyasar عبر `_sync_moyasar_batch(batch_ref)`. مهاجم يرسل حمولة مزوّرة **لا يستطيع تفعيل اشتراك** — هذا نمط صحيح ومحمود.

المتبقي: من يخمّن `batch_ref` يجبر الخادم على نداء خارجي، وفرق الاستجابة بين `502` و`{"ok": true}` يكشف وجود المرجع. `batch_ref` هو `uuid4().hex[:16]` (64 بت) فالتخمين غير عملي، والحدّ 60/دقيقة/IP قائم.

**Recommended Fix**
أضف توكنًا مشتركًا في المسار وقارنه بـ `hmac.compare_digest` — كما هو مطبَّق بالفعل ومصحّح في `tamara_webhook` ([reports/tamara_gateway.py:230](reports/tamara_gateway.py#L230)). ووحّد رمز الاستجابة إلى `200` دائمًا لمنع الاستكشاف.

---

### SEC-009 — `mansour_assistant_reply` عام و`@csrf_exempt` ويحمل تكلفة مالية

| | |
|---|---|
| **Severity** | 🔵 **Low** |
| **Affected File** | [reports/views/mansour.py:113-116](reports/views/mansour.py#L113-L116) |
| **Affected Endpoint** | `POST /mansour/reply/` |
| **CWE** | CWE-352 / CWE-770 |

**Description**
نقطة عامة تستدعي واجهة OpenAI المدفوعة، معفاة من CSRF. الحمايات القائمة **جيدة**: 50/يوم/IP + سقف يومي عام (`MANSOUR_ASSISTANT_DAILY_GLOBAL_LIMIT=2000`) + سقف `max_output_tokens` + مهلة.

المتبقي: لكون النقطة `csrf_exempt`، يستطيع موقع خارجي إرسال POST من متصفحات زوّاره لاستهلاك الميزانية بعناوين ضحايا موزَّعة. وتذكّر SEC-002: السقف اليومي نفسه يقرأ من الكاش، فيختفي عند تعثّر Redis.

**Recommended Fix**
اشترط أن يكون الطلب من نفس الأصل.

**Example Fix**

```python
# reports/views/mansour.py — بعد فحص content_type
_ALLOWED_FETCH_SITES = {"same-origin", "same-site", "none"}

fetch_site = (request.headers.get("Sec-Fetch-Site") or "").lower()
if fetch_site and fetch_site not in _ALLOWED_FETCH_SITES:
    return _json_response({"ok": False, "message": "طلب غير مسموح."}, status=403)
```

---

### SEC-010 — احتفاظ سجل التدقيق 30 يومًا فقط

| | |
|---|---|
| **Severity** | 🔵 **Low** |
| **Affected File** | [config/settings.py:881](config/settings.py#L881) |
| **CWE** | CWE-778 (Insufficient Logging) |

**Description**
`AUDIT_LOG_RETENTION_DAYS = 30` مع مهمة تنظيف يومية. سجل التدقيق نفسه **ممتاز البنية** — يحفظ الفاعل والمدرسة والقيمة القديمة والجديدة ولقطة اسم الفاعل التي تصمد بعد حذف الحساب ([reports/model_parts/audit.py](reports/model_parts/audit.py)).

المشكلة في المدة: نزاع تجاري أو تحقيق في حادثة أمنية نادرًا ما يُكتشف خلال 30 يومًا. من يمسح أثره اليوم يكفيه الانتظار شهرًا.

**Recommended Fix**

```bash
# deploy/hetzner/env.production
AUDIT_LOG_RETENTION_DAYS=365
```
مع أرشفة السجلات الأقدم من 90 يومًا إلى R2 قبل الحذف لضبط حجم الجدول.

---

### SEC-011 — مفاتيح Redis دائمة (بلا TTL) تنمو مع عدد المدارس

| | |
|---|---|
| **Severity** | ⚪ **Info** |
| **Affected File** | [reports/cache_utils.py:91](reports/cache_utils.py#L91), [109](reports/cache_utils.py#L109) |

**Description**

```python
cache.add(key, 1, timeout=None)   # school_dashboard:version:{school_id}
```

تحت `maxmemory-policy volatile-lru`، المفاتيح **بلا TTL غير قابلة للإخلاء إطلاقًا**. عددها يساوي عدد المدارس ولا يتناقص أبدًا.

**حساب الأثر الفعلي:** ~80 بايت للمفتاح الواحد.
| عدد المدارس | استهلاك الذاكرة |
|---|---|
| 100 | 8 KB |
| 1,000 | 80 KB |
| 5,000 | 400 KB |

مقابل `maxmemory=384mb`، الأثر **مهمَل عمليًا**. يُسجَّل للاكتمال لا للإصلاح العاجل. الإصلاح إن أُريد: `timeout=30*24*3600` — تصفير الإصدار غير ضار لأنه يُعاد إنشاؤه.

---

### SEC-012 — نسخ SQLite تحمل بيانات في مجلد العمل

| | |
|---|---|
| **Severity** | ⚪ **Info** |
| **Affected File** | `db.sqlite3` (17.8 MB), `db.sqlite3.backup-before-roles-migration` (17.8 MB) |

**Description**
ملفان بحجم 17.8 MB لكلٍّ منهما في جذر المشروع. **كلاهما مستبعد من Git** (تحقّقناه: `git check-ignore` → `.gitignore:67`)، فلا تسريب عبر المستودع.

المخاطرة تشغيلية بحتة: إن كانا يحملان بيانات مدارس حقيقية، فهي **نسخة غير مشفَّرة من بيانات المستأجرين على جهاز محمول**. يُنصح بحذف نسخة الاحتياط بعد التأكد من نجاح الترحيل، وباستخدام بيانات مصطنعة للتطوير.

---

## 5 — مصفوفة الصلاحيات (Permission Matrix)

مستخرَجة من [reports/permissions.py](reports/permissions.py) و[reports/capabilities.py](reports/capabilities.py). النطاق ضمنيًا **المدرسة النشطة** في كل صف مدرسي.

| الدور | Read | Create | Update | Delete | Export | Manage Users | النطاق |
|---|---|---|---|---|---|---|---|
| **Superuser (مالك النظام)** | ✅ الكل | ✅ | ✅ | ✅ | ✅ | ✅ | المنصة كاملة |
| **School Manager (مدير المدرسة)** | ✅ مدرسته | ✅ | ✅ | ✅ | ✅ | ✅ | مدرسته فقط |
| **Deputy (وكيل)** | 🟡 حسب النطاق | 🟡 | 🟡 | ❌ | 🟡 | ❌ | أقسام نطاقه |
| **Admin Staff (موظف إداري)** | 🟡 حسب النطاق | 🟡 | 🟡 | ❌ | ❌ | ❌ | أقسام نطاقه |
| **Lab Technician (محضّر مختبر)** | ✅ المختبر | ✅ المختبر | ✅ المختبر | ❌ | ❌ | ❌ | المختبر |
| **Department Officer (رئيس قسم)** | ✅ تقارير قسمه | ✅ | ✅ قسمه | ✅ قسمه | ✅ | ❌ | أنواع تقارير قسمه |
| **Department Member (عضو قسم)** | ✅ عرض فقط | ✅ خاصته | ❌ | ❌ | ❌ | ❌ | أنواع تقارير قسمه |
| **Teacher (معلّم)** | ✅ تقاريره | ✅ | ✅ تقاريره | ✅ تقاريره | ❌ | ❌ | حسابه |
| **Executive Director (مدير تنفيذي)** | ✅ مؤشرات المجموعة | ❌ | ❌ | ❌ | 🟡 مجمَّع | ❌ | مدارس مجموعاته |
| **Delegate (مفوَّض مؤقتًا)** | 🟡 المفوَّض | 🟡 | 🟡 | ❌ | 🟡 | ❌ | ينتهي زمنيًا |

### فحص التصعيد الرأسي (Vertical Privilege Escalation)

| المسار | النتيجة | الدليل |
|---|---|---|
| Teacher → Manager | ❌ محجوب | `role_required({"manager"})` يستعلم `SchoolMembership` مباشرة، لا يقرأ عَلَمًا |
| Admin Staff → Manager | ❌ محجوب | `is_school_manager` مصدره الوحيد `role_type=MANAGER` |
| Deputy → Manager | ❌ محجوب | [permissions.py:322-327](reports/permissions.py#L322-L327) — `is_school_deputy` **لا تُستدعى من** `is_school_manager` (موثَّق ويحرسه اختبار) |
| School Manager → Superuser | ❌ محجوب | `platform_allowed_schools_qs` تعيد `School.objects.none()` لغير الـ superuser |
| Executive Director → School Manager | ❌ محجوب | [permissions.py:699-707](reports/permissions.py#L699-L707) — عزل صريح موثَّق ومحروس باختبار |
| `is_staff` → صلاحية مستأجر | ❌ محجوب | [services_reports.py:209-212](reports/services_reports.py#L209-L212) — الشرط صُحّح من `is_staff` إلى `is_superuser` |

### فحص التصعيد الأفقي (Horizontal)

- `capability_required` و`role_required` يقرآن `active_school_id` من **الجلسة** لا من مُدخل المستخدم.
- `ActiveSchoolGuardMiddleware` سبق أن أثبت العضوية قبل وصول الطلب لأي عرض.
- `Delegation.clean()` ([scopes.py:237-245](reports/model_parts/scopes.py#L237-L245)) يرفض تفويض من ليس عضوًا في نفس المدرسة.
- `can_send_group_notification` ([permissions.py:775-789](reports/permissions.py#L775-L789)) يشترط `issubset` كاملًا ويرفض التقاطع الجزئي — التنفيذ المنقوص يوهم المرسِل بوصول لم يحدث.

---

## 6 — فحص الحقن (Injection)

| النوع | النتيجة | الدليل |
|---|---|---|
| SQL Injection | ✅ **غير موجود** | صفر `.raw()`، صفر `cursor.execute` على مدخلات. المواضع الأربعة الوحيدة ثوابت: `SELECT 1`, `SHOW max_connections`, `SELECT count(*) FROM pg_stat_activity` — كلها في `production_preflight.py` و`core/views.py:84` |
| Command / OS Injection | ✅ **غير موجود** | صفر `os.system`، صفر `subprocess`، صفر `shell=True` |
| Code Injection | ✅ **غير موجود** | صفر `eval`، صفر `exec`، صفر `pickle.loads`، صفر `__import__` ديناميكي |
| SSTI | ✅ **غير موجود** | لا `Template(user_input)`؛ كل التصيير عبر ملفات قوالب |
| Path Traversal | ✅ محمي | [base.py:64-70](reports/model_parts/base.py#L64-L70) — `os.path.basename` + `get_valid_filename` + إعادة تسمية إجبارية بـ `token_hex(8)` |
| XXE | ✅ غير قابل للتطبيق | لا تحليل XML لمدخلات المستخدم |
| SSRF | 🟡 محدود ومضبوط | 4 مواضع `urlopen`، كلها إلى عناوين من `settings` لا من مدخلات. Web Push مقيّد بـ `WEB_PUSH_ALLOWED_ENDPOINT_HOSTS` ([settings.py:300-309](config/settings.py#L300-L309)) — **قائمة سماح صريحة، وهو الصواب** |
| Header / CRLF Injection | ✅ محمي | [urls.py:74-77](config/urls.py#L74-L77) ينزع `\r` و`\n` من بريد الأمان صراحةً |
| LDAP / NoSQL | ✅ غير قابل للتطبيق | لا LDAP ولا NoSQL |

---

## 7 — فحص XSS

- **Stored XSS:** لم يُعثر عليه. القوالب تعتمد الهروب التلقائي في Django. `mark_safe` تظهر في موضعين فقط، وكلاهما على محتوى مملوك للمستودع لا على مدخلات مستخدم ([templatetags/maintenance_text.py:73](reports/templatetags/maintenance_text.py#L73), [pdf_user_guide.py:32](reports/pdf_user_guide.py#L32)) — راجعناهما يدويًا وكلاهما آمن. تنبيهات Bandit `B308/B703` عليهما **False Positive**.
- **Reflected XSS:** لم يُعثر عليه.
- **DOM XSS:** CSP بـ nonce تمنع التنفيذ حتى لو وُجدت بدائية حقن. `object-src 'none'` و`base-uri 'self'` مضبوطان.
- **رفع SVG/HTML:** محجوب صراحةً — [validators.py:77-84](reports/validators.py#L77-L84): `BLOCKED_EXTS` و`BLOCKED_MIME_PREFIXES` يشملان `.svg`, `.html`, `.js`.

**ملاحظة تشغيلية مهمة:** `python-magic` يعتمد `libmagic` الأصلية. `Dockerfile` يثبّت `libmagic1` صراحةً مع تعليق يشرح أن غيابها يُسقط شمّ المحتوى صامتًا — **إعداد صحيح ومقصود.**

---

## 8 — فحص CSRF

- `CsrfViewMiddleware` مفعّل ([settings.py:532](config/settings.py#L532)).
- `CSRF_COOKIE_SECURE`, `CSRF_COOKIE_HTTPONLY`, `CSRF_COOKIE_SAMESITE=Lax` في الإنتاج.
- `CSRF_TRUSTED_ORIGINS` مشتقّ من `ALLOWED_HOSTS` مع بروتوكول `https` صريح.
- `CSRF_FAILURE_VIEW` مخصّص لتجربة مفهومة.
- `form-action 'self' https://checkout.moyasar.com` — CSP تحرس سلسلة إعادة التوجيه كاملة، والتعليق في [middleware.py:777-782](reports/middleware.py#L777-L782) يوثّق أن بوابة مفعّلة بلا أصلها هنا يُحجب دفعها صامتًا.
- `@csrf_exempt` في **موضع واحد فقط**: `mansour_assistant_reply` — راجع SEC-009.
- الويب هوك (`tamara_webhook`, `moyasar_callback`) خارج CSRF بحكم طبيعتها، ومحميّة بالتوكن/إعادة الاشتقاق.

---

## 9 — الأداء واستهلاك الموارد

### فهارس قاعدة البيانات — الجداول الساخنة

[reports/model_parts/reports.py:98-114](reports/model_parts/reports.py#L98-L114) — فهارس مركّبة **مطابقة لأنماط الاستعلام الفعلية**، وكل واحد موثَّق بالاستعلام الذي يخدمه:

```python
models.Index(fields=["teacher", "school", "-report_date", "-id"])   # my_reports
models.Index(fields=["school", "-report_date", "-id"])              # admin_reports
models.Index(fields=["school", "academic_year", "-report_date", "-id"])
```

[reports/model_parts/notifications.py:176-182](reports/model_parts/notifications.py#L176-L182) — `NotificationRecipient` وهو أسرع الجداول نموًّا:

```python
models.Index(fields=["teacher", "is_read", "-created_at"])
models.Index(fields=["teacher", "is_signed", "-created_at"])
models.Index(fields=["notification", "teacher"])
unique_together = (("notification", "teacher"))   # ← يمنع التكرار على مستوى القاعدة
```

**هذا مستوى نضج عالٍ.** الفهارس مشتقّة من الاستعلامات لا مضافة عشوائيًا.

### مكافحة N+1 — صريحة ومقصودة

[reports/permissions.py:120-189](reports/permissions.py#L120-L189) — `prefetch_memberships_for_school` تجلب كل العضويات باستعلام واحد وتملأ **كل** المفاتيح التي تسألها `effective_user_role_label`، مع تعليق يوثّق أن نسخة سابقة كانت تملأ مفتاحين فقط فعاد الكشف إلى N+1 صامتًا.

### حماية من Cache Stampede

[reports/cache_utils.py:144-212](reports/cache_utils.py#L144-L212) — `get_school_dashboard_payload`: قفل Redis غير حاجز + نسخة قديمة صالحة + انتظار محدود (1.5s) + مسار احتياطي عند كسر القفل. **تنفيذ صحيح ومكتمل.**

### سقف التزامن

[core/middleware.py:84-189](core/middleware.py#L84-L189) — `ConcurrencyLimitMiddleware` مع تعليل دقيق: تحت ASGI يحصل كل طلب على خيط واتصال قاعدة بيانات خاص به، فبرزخ 1000 زائر = 1000 اتصال، وPostgreSQL يرفض بعد `max_connections`. السقف مشتق آليًا:

```
MAX_CONCURRENT_REQUESTS = (DB_MAX_CONNECTIONS - DB_RESERVED_CONNECTIONS) / WEB_CONCURRENCY
```

### تحليل السعة (Capacity) — تحليلي، بلا Load Test على الإنتاج

بيئة الاختبار (Staging) غير متوفرة، فلم يُنفَّذ اختبار حمل. التحليل من الإعدادات الفعلية:

| المتزامنون | التقييم | العنق المتوقّع |
|---|---|---|
| 100 | ✅ مريح | لا شيء |
| 500 | ✅ مقبول | اتصالات PostgreSQL — يتولاها سقف التزامن بالتخفيف |
| 1,000 | 🟡 يتطلب `WEB_CONCURRENCY≥4` + رفع `cpus` | PostgreSQL + ذاكرة الويب (`mem_limit: 1536m` ≈ 250MB/عامل) |
| 2,000 | 🟠 يتطلب PgBouncer (الـ profile جاهز) | تجمّع اتصالات القاعدة |
| 5,000 | 🔴 يتطلب توسّعًا أفقيًا + فصل Redis | Redis (`maxmemory: 384mb`) وطابور Celery |

**تقدير ذاكرة Redis:** الجلسات ≈ 1KB لكل جلسة نشطة. عند 5,000 مدرسة × 25 منسوبًا × 20% تزامن ≈ 25,000 جلسة ≈ 25 MB، زائد الكاش والطوابير. الحد 384 MB **كافٍ حتى ~2,000 مدرسة**؛ العتبات موثّقة أصلًا في [settings.py:639-654](config/settings.py#L639-L654) وهناك مهمة مراقبة كل 5 دقائق (`monitor-infrastructure-capacity`) تنبّه عند 80%.

### مخاطر استنزاف الموارد

| المتجه | الحماية القائمة | التقييم |
|---|---|---|
| Pagination ضخمة | لا `per_page` من مدخل المستخدم إطلاقًا (تحقّقنا: صفر مواضع) | ✅ محمي |
| رفع ملفات ضخمة | `MAX_IMAGE_MB=10`, `MAX_ATTACHMENT_MB=5`, `DATA_UPLOAD_MAX_MEMORY_SIZE=10MB`, `DATA_UPLOAD_MAX_NUMBER_FILES=20` | ✅ محمي |
| تصدير ضخم | ZIP مُفرَّغ إلى Celery (`HEAVY_EXPORT_ASYNC_ENABLED`)، حدّ 6/ساعة/مستخدم | ✅ محمي |
| توليد PDF | مُفرَّغ إلى `worker-media` بمهلة 45s و`max-tasks-per-child=50` (لتشظّي ذاكرة WeasyPrint) | ✅ محمي |
| ReDoS | لا Regex على مدخلات غير محدودة | ✅ محمي |
| عاصفة WebSocket | 3 اتصالات/مستخدم، 10 اتصالات/دقيقة، مهلة خمول 75s | ✅ محمي |
| إساءة على مستوى المستأجر | `SCHOOL_RATE_LIMIT_REQUESTS=900/60s` | ✅ محمي |
| **سقوط Redis** | — | ❌ **كل ما سبق يعتمد على الكاش — راجع SEC-002** |

---

## 10 — OWASP Top 10 (2021) — التغطية

| # | الفئة | الحالة | التعليق |
|---|---|---|---|
| A01 | Broken Access Control | 🟢 قوي | ثلاث طبقات مستقلة، فشل مغلق، لا IDOR |
| A02 | Cryptographic Failures | 🟡 مقبول | HTTPS مفروض، تجزئة Django القياسية، توكنات 256 بت — **خصم على SEC-001** |
| A03 | Injection | 🟢 قوي | صفر متجهات |
| A04 | Insecure Design | 🟢 قوي | الفشل المغلق مبدأ معماري مطبَّق وموثَّق |
| A05 | Security Misconfiguration | 🟡 مقبول | `check --deploy` نظيف — **خصم على SEC-006** |
| A06 | Vulnerable Components | 🟡 مقبول | المباشرة نظيفة — **خصم على SEC-004** |
| A07 | Auth Failures | 🟢 قوي | حدّان، مقاومة timing، passkeys — **خصم على SEC-002** |
| A08 | Software/Data Integrity | 🟢 قوي | الدفع يُتحقَّق من جهة الخادم دائمًا، HMAC بمقارنة ثابتة الزمن |
| A09 | Logging Failures | 🟡 مقبول | سجل تدقيق ممتاز — **خصم على SEC-003 و SEC-010** |
| A10 | SSRF | 🟢 قوي | قائمة سماح صريحة لوجهات Web Push |

### OWASP API Security Top 10

| # | الفئة | الحالة |
|---|---|---|
| API1 | BOLA | 🟢 كل ViewSet مفلتر بالمستأجر في `get_queryset` |
| API2 | Broken Authentication | 🟢 `IsAuthenticated` افتراضي عالمي |
| API3 | Excessive Data Exposure | 🟢 Serializers صريحة الحقول (75 سطرًا فقط) |
| API4 | Resource Consumption | 🟡 مضبوط — راجع SEC-002 |
| API5 | BFLA | 🟢 `IsTenantMember` + `restrict_queryset_for_user` |
| API6 | Sensitive Business Flows | 🟢 الدفع بتحقق من جهة الخادم |
| API7 | SSRF | 🟢 قائمة سماح |
| API8 | Misconfiguration | 🟡 راجع SEC-006 |
| API9 | Improper Inventory | 🟢 5 ViewSets فقط، جرد كامل |
| API10 | Unsafe API Consumption | 🟢 مهلات مضبوطة على كل نداء خارجي |

---

## 11 — الإجابات الصريحة على أسئلتك الاثني عشر

### 1. هل يوجد احتمال أن تصل مدرسة إلى بيانات مدرسة أخرى؟
**لا — لم يُعثر على أي مسار.** العزل مفروض على ثلاث طبقات مستقلة، وكل واحدة تفشل مغلقة. المسار الوحيد المتبقّي نظريًا هو **تسريب رابط وسائط موقَّع** (SEC-005)، وهو يتطلب أن يسلّم مستخدمٌ من المدرسة الرابطَ بنفسه، وقابل للتقليص إلى 15 دقيقة بتغيير سطر واحد.

### 2. هل يوجد IDOR أو Broken Access Control؟
**لا.** فُحص 135 موضع جلب كائن. كل موضع إمّا يقيّد الـ QuerySet قبل الجلب، أو يفحص `school_id` بعده صراحةً. صفر ثغرات.

### 3. هل العزل Multi-Tenant مطبَّق بشكل صحيح؟
**نعم — وبمستوى أعلى من المعتاد.** الدور عضوية مُنطَقة بالمدرسة لا عَلَم على الحساب، وفلتر المستأجر مطويّ داخل `restrict_queryset_for_user` نفسها فلا يعتمد على انضباط المستدعي، وغياب السياق يضيّق النطاق لا يوسّعه.

### 4. هل Cache يمكن أن يسبب تسريب بيانات بين المستخدمين أو المدارس؟
**لا.** كل مفتاح كاش في المشروع يحمل `school_id` و/أو `user_id`. لا يوجد مفتاح مجرَّد واحد. `/` يُرسَل بـ `no-store, private` و`vary: Cookie` فلا تخزّن Cloudflare استجابة مستخدم لآخر.

### 5. هل Redis آمن ومضبوط بطريقة صحيحة؟
**آمن شبكيًا — نعم قطعًا.** `requirepass` مفعّل، الشبكة `internal: true`، لا منفذ منشور، `maxmemory` و`maxmemory-policy` مضبوطان بتعليل صحيح، `appendonly yes`.
**مضبوط وظيفيًا — لا كليًّا.** مثيل واحد يحمل الكاش والجلسات والطوابير وعدّادات الحدود معًا، و`volatile-lru` يُخلي عدّادات الحدود صامتًا تحت الضغط، و`IGNORE_EXCEPTIONS: True` يجعل الحدود تفشل مفتوحة (**SEC-002**).

### 6. هل توجد ثغرات Critical أو High؟
**Critical: لا — صفر.**
**High: نعم — اثنتان:** SEC-001 (أسرار في تاريخ Git) و SEC-002 (فشل مفتوح عند تعثّر Redis).

### 7. هل يوجد Secret أو Token مكشوف؟
**في الكود: لا.** صفر أسرار مبرمَجة، صفر أسرار في حزمة الواجهة، صفر في صفحات الخطأ.
**في تاريخ Git: نعم** — `SECRET_KEY` إنتاجي و`DATABASE_URL` ومفاتيح Cloudinary (SEC-001). **مُخفَّف بأن المستودع خاص**، وبأن `.env` الحالي تطويري بحت (`sk_test_`, `dev-unsafe`). يجب التحقق والتدوير.

### 8. هل توجد Endpoints يمكن استغلالها لاستهلاك موارد الخادم؟
**لا في الظروف الطبيعية.** الحماية شاملة: سقف تزامن، ميزانية لكل مستأجر، حدود لكل مستخدم/IP، تفريغ PDF وZIP للخلفية، حدود رفع، وصفر مواضع تقبل `per_page` من المستخدم.
**نعم إذا تعثّر Redis** — كل ما سبق يقرأ من الكاش (SEC-002).

### 9. هل قاعدة البيانات مهيأة للنمو؟
**نعم.** الفهارس المركّبة مشتقّة من أنماط الاستعلام الفعلية وموثَّقة بها. `unique_together` على `NotificationRecipient`. تنظيف دوري للتدقيق والجلسات. `CONN_MAX_AGE=0` بتعليل صحيح تحت ASGI. profile الـ PgBouncer جاهز للتفعيل بأمر واحد.

### 10. هل المنصة تتحمل نمو عدد المدارس والمستخدمين؟
**نعم حتى ~500 مدرسة على البنية الحالية دون أي تغيير.** حتى 1,000 برفع `WEB_CONCURRENCY`. بعد 2,000 يلزم PgBouncer وفصل Redis — والعتبات وخطوات التوسع **موثَّقة أصلًا في `settings.py` نفسها**، وهو ما يدلّ على تخطيط سعة حقيقي لا ارتجال.

### 11. ما أول 10 مشاكل يجب إصلاحها؟
راجع [CRITICAL_FIXES.md](CRITICAL_FIXES.md) — مرتّبة بـ P0/P1/P2/P3.

### 12. هل تنصح بإطلاق المنصة تجاريًا الآن؟
**نعم — بعد إصلاح 4 بنود لا تتجاوز يومَي عمل.**

لا يوجد سبب معماري يمنع الإطلاق. العزل والصلاحيات — وهما الأخطر — مبنيان بشكل صحيح. البنود الأربعة الحاجزة تشغيلية بحتة: تدوير سرّ، إغلاق مسار fail-open، تجزئة سطر سجلّ، وتقصير عمر رابط. لا يمسّ أيٌّ منها المعمارية ولا منطق العمل.

---

## 12 — القرار النهائي

# 🟡 READY AFTER FIXES

### الأسباب التقنية التي أدّت إلى هذا القرار

**ما يمنع 🟢 READY FOR PRODUCTION:**

1. **SEC-001 — `SECRET_KEY` إنتاجي في تاريخ Git.** ما دام لم يُتحقَّق من أن المفتاح الحالي يختلف عن المسرَّب، يبقى احتمال انتحال كامل للهوية قائمًا. هذا وحده يمنع إعلان الجاهزية.
2. **SEC-002 — الحماية تفشل مفتوحة.** منصة تجارية تحمل بيانات مدارس لا يجوز أن تفقد كل حدودها ضد التخمين والإساءة بصمت لأن Redis تحت ضغط. الخطورة تتحقق **وقت الذروة** — أي في اليوم الذي يهمّك.
3. **SEC-003 — أرقام هوية وجوالات في سجلات النصّ الصريح.** التزام PDPL يقتضي إغلاق هذا قبل استقبال مستخدمين حقيقيين.
4. **SEC-005 — روابط وسائط تعيش 24 ساعة.** المسار الوحيد المتبقّي لوصول عابر للمستأجرين.

**ما يمنع 🔴 NOT READY FOR PRODUCTION** — أي: لماذا هذه ليست منصة غير جاهزة:

1. **صفر ثغرات Critical.** لا تجاوز صلاحيات، لا حقن، لا انتحال.
2. **العزل — أخطر ما في المنصة — مبنيّ بشكل صحيح على ثلاث طبقات مستقلة**، وليس بانضباط المطوّر في كل عرض.
3. **صفر متجهات حقن** عبر 53,313 سطرًا.
4. **البنية التحتية مصلَّبة فعليًا:** `cap_drop: ALL`, `no-new-privileges`, شبكة داخلية، صفر منافذ مكشوفة، مستخدم غير جذري، Cloudflare بتحدٍّ مفعّل.
5. **الدفع يُتحقَّق من جهة الخادم دائمًا** — حمولة الويب هوك لا تُصدَّق أبدًا.
6. **109 ملف اختبار** موجودة بالفعل، منها اختبارات صريحة للعزل والأدوار.
7. **الإعدادات الإنتاجية ترفض التشغيل عند الخطأ** — `PRODUCTION_STRICT_MODE` يرمي `ImproperlyConfigured` على SQLite وعلى الوسائط العامة وعلى بريد غير SMTP.
8. **تخطيط السعة حقيقي وموثَّق** — عتبات التوسع مكتوبة في `settings.py` بأرقام لا بأمنيات.

**الحكم:** المشكلات المكتشفة **تشغيلية لا معمارية**. إصلاح البنود الأربعة يرفع التقييم إلى 🟢 دون لمس سطر واحد من منطق العمل.

**الزمن المقدَّر للوصول إلى 🟢:** يوم إلى يومَي عمل.

---

*انتهى التقرير. راجع [CRITICAL_FIXES.md](CRITICAL_FIXES.md) للأولويات و[SECURITY_REMEDIATION_PLAN.md](SECURITY_REMEDIATION_PLAN.md) لخطة التنفيذ.*
