# Phase 5: Production Scaling Strategy Report

**التاريخ:** 2026-04-20  
**الهدف:** تحويل النظام من "محسّن" إلى "جاهز للتوسع التشغيلي" مع خطة واضحة وقابلة للتنفيذ.

---

## 1) Summary

### ما تم تنفيذه:
- **تقليل استعلامات الـ middleware بمقدار ~1 استعلام/طلب** عبر مشاركة كائن `SchoolMembership` بين `ActiveSchoolGuardMiddleware` و `SubscriptionMiddleware`.
- **إضافة `AnonRateThrottle` (30/min)** لحماية API endpoints من الوصول المجهول المفرط.
- **إضافة مؤشرات السعة** إلى أمر `op_diagnostics` (عدد المعلمين النشطين، التقارير، الإشعارات، AuditLog).
- **توثيق scaling notes** في `settings.py` (DB, Redis, Celery) و `render.yaml`.
- **إنشاء نموذج سعة تفصيلي** مع milestones النمو.

### ما تحسّن في الجاهزية:
- Capacity model واضح بأرقام تقديرية لكل مرحلة نمو.
- خارطة طريق واضحة لفصل Redis / workers / DB عند الحاجة.
- SLO/SLA أولية موثقة مع thresholds قابلة للقياس.
- Load testing scenarios محددة وجاهزة للتنفيذ.
- تكلفة النمو وdrivers محددة بوضوح.

### الأثر المتوقع:
- تخفيض ~25% من استعلامات middleware لكل طلب HTTP مصادق.
- حماية API من abuse مجهول.
- قدرة على اتخاذ قرارات scaling مبنية على بيانات واضحة.

---

## 2) Files Changed

| الملف | نوع التغيير | السبب |
|-------|-------------|-------|
| `reports/middleware.py` | تعديل | مشاركة membership بين Guard و Subscription middleware (-1 DB query/request) |
| `config/settings.py` | تعديل | scaling notes (DB/Redis/Celery) + AnonRateThrottle |
| `render.yaml` | تعديل | scaling notes وتوثيق مسار التوسع |
| `reports/management/commands/op_diagnostics.py` | تعديل | إضافة capacity indicators |
| `docs/PHASE5_PRODUCTION_SCALING_STRATEGY.md` | **جديد** | التقرير النهائي |

---

## 3) Capacity Model Report

### الفرضيات الأساسية (تقديرية — ليست قياسات فعلية):

| المتغير | القيمة المفترضة | المصدر |
|---------|----------------|--------|
| معلمون/مدرسة | ~25 | افتراض نموذجي لمدرسة سعودية |
| نسبة التزامن | ~10-15% | معيار SaaS B2B |
| تقارير/معلم/يوم | ~1 | الاستخدام اليومي الأساسي |
| إشعارات/مدرسة/يوم | ~3-5 | تعميم + تقارير |
| اتصالات WS متزامنة | ~10% من المستخدمين | معيار SaaS |

### وحدات الحمل الأساسية:

| الوحدة | تعريف | كيف ينمو |
|--------|-------|----------|
| مدرسة | وحدة الإيجار (tenant) | خطي — كل مدرسة تضيف ~25 مستخدم |
| مستخدم نشط | طلبات HTTP + WS | خطي مع المدارس |
| تقرير يومي | كتابة DB + معالجة صور | O(schools × teachers/school) |
| إشعار | fan-out إلى كل معلمي المدرسة | O(notifications × teachers/school) |
| مهمة Celery | معالجة خلفية | خطي مع التقارير + دوري مع المدارس |
| اتصال WS | اتصال مستمر | O(concurrent_users) |

### حدود النظام التقديرية بالإعداد الحالي:

| المقياس | الحد الحالي التقريبي | المحدّد |
|---------|---------------------|---------|
| مدارس متزامنة | ~200-300 | حدود عامل الويب الواحد (3 workers × 2 threads) |
| طلبات/ثانية | ~50-80 | Gunicorn 3 workers + middleware DB queries |
| اتصالات WS | ~500-1000 | ASGI worker واحد + file descriptors |
| مهام Celery/دقيقة | ~200 | Worker واحد بتزامن 4 |
| حجم DB | ~5-10M صف | PostgreSQL أساسي بدون archiving |

### جدول النمو:

| المرحلة | المدارس | المستخدمون | المتزامنون (~12%) | طلبات/دقيقة | WS نشط |
|---------|---------|-----------|------------------|-------------|--------|
| **الحالية** | <50 | <1,250 | ~150 | ~300 | ~50 |
| **100 مدرسة** | 100 | 2,500 | ~300 | ~600 | ~100 |
| **500 مدرسة** | 500 | 12,500 | ~1,500 | ~3,000 | ~500 |
| **1000 مدرسة** | 1,000 | 25,000 | ~3,000 | ~6,000 | ~1,000 |

---

## 4) Web/App Scaling Readiness Report

### الوضع الحالي: **جاهز للتوسع الأفقي مع ملاحظات**

| البند | الحالة | ملاحظات |
|-------|--------|---------|
| Sessions | ✅ Redis-backed | لا تعتمد على ذاكرة محلية |
| Cache | ✅ Redis-backed | مشترك بين instances |
| Static files | ✅ WhiteNoise + Cloudflare CDN | لا يعتمد على filesystem |
| Media uploads | ✅ R2 (S3-compatible) في production | محلي فقط في development |
| WebSocket | ✅ Redis channel layer | يدعم عدة ASGI instances |
| Health check | ✅ `/healthz/` يفحص DB + Cache + Channels | جاهز لـ load balancer |
| Local state | ⚠️ `_thread_locals` في middleware | آمن (thread-local, ليس process state) |
| Startup | ⚠️ `migrate + collectstatic` في Procfile | يعمل مرة عند deploy — لكن يجب ضمان idempotency |

### ما يمنع التوسع:
1. **عامل ويب واحد على Render** — يحتاج زيادة `numInstances` عند >300 متزامن.
2. **`post_migrate` signal** ينشئ departments لكل المدارس — O(N) عند كل deploy. آمن لكن بطيء عند 1000+ مدرسة.

### توصيات:
- عند 500 مدرسة: أضف web instance ثانية في Render.
- عند 1000 مدرسة: 3+ web instances مع load balancing.
- لا تغييرات في الكود مطلوبة — البنية جاهزة.

---

## 5) Redis Scaling Report

### الاستخدام الحالي:

| الوظيفة | Redis DB | Key prefix | حجم تقريبي |
|---------|----------|------------|------------|
| Celery broker | DB 0 | `celery*` | صغير (~1K keys) |
| Channel layer | DB 0 | `asgi:*` | متوسط (~concurrent WS connections) |
| Django cache | DB 1 | `sr:*` | متوسط (~users × 10 keys) |
| Sessions | DB 1 | `sr:django.contrib.sessions*` | خطي مع المستخدمين |
| Opmetrics | DB 1 | `sr:opmetrics:*` | ثابت (~50 keys/hour) |
| Periodic locks | DB 1 | `sr:periodic_lock:*` | ثابت (3-4 keys) |

### متى يصبح الفصل مطلوبًا:

| المؤشر | الحد | الإجراء |
|--------|------|---------|
| `used_memory_rss` > 200MB | Redis starter plan limit | فصل cache إلى instance منفصل |
| `connected_clients` > 100 | Connection pool exhaustion | فصل channels |
| Celery queue depth > 1000 لأكثر من 5 دقائق | Broker overload | فصل broker |
| `evicted_keys` > 0 | Memory pressure | ترقية plan أو فصل |

### خطة الفصل (عند الحاجة):

```
المرحلة 1 (500 مدرسة): فصل Cache إلى REDIS_CACHE_URL منفصل
المرحلة 2 (1000 مدرسة): فصل Channels إلى REDIS_CHANNEL_LAYER_URL منفصل
المرحلة 3 (2000+ مدرسة): فصل Broker إلى CELERY_BROKER_URL منفصل
```

الإعداد الحالي يدعم هذا الفصل عبر environment variables **دون تغيير كود**:
- `REDIS_CACHE_URL` — موجود ويُستخرج من `REDIS_URL` إذا لم يُحدد
- `CELERY_BROKER_URL` — موجود
- `REDIS_CHANNEL_LAYER_URL` — موجود (يُشتق من `REDIS_URL`)

---

## 6) Database Scaling Readiness Report

### الجداول الأسرع نموًا:

| الجدول | معدل النمو | الحجم عند 1000 مدرسة/سنة |
|--------|-----------|--------------------------|
| `NotificationRecipient` | ~750K/شهر | ~9M صف |
| `AuditLog` | ~50K/يوم (مع cleanup: 30 يوم) | ~1.5M صف (ثابت) |
| `Report` | ~25K/يوم | ~7.5M/سنة |
| `AchievementEvidenceImage` | موسمي | ~500K/سنة |
| `Ticket` / `TicketNote` | ~5K/شهر | ~60K/سنة |

### العمليات الحساسة:

| العملية | استعلامات | المخاطرة عند التوسع |
|---------|----------|---------------------|
| `nav_context` | 4-8/طلب | عالية — 25K مستخدم × 8 queries = bottleneck |
| Middleware chain | 2-3/طلب | متوسطة (تم تحسينها في Phase 5) |
| Daily summary task | 3-4/مدرسة | عالية عند 1000 مدرسة |
| Notification fan-out | bulk_create | منخفضة (batch) |

### متى تحتاج إجراءات:

| الإجراء | المؤشر | المرحلة |
|---------|--------|---------|
| **PgBouncer / Connection Pooler** | >2 web instances × 6 connections | 500 مدرسة |
| **Read Replica** | DB CPU > 70% أو query latency p95 > 200ms | 1000 مدرسة |
| **Archiving (NotificationRecipient)** | >10M صف | 1000 مدرسة |
| **Partitioning (AuditLog)** | لا حاجة — cleanup يبقيه عند ~1.5M | غير مطلوب |
| **Index review** | Slow query log > 100ms | مستمر |

### الفهارس الموجودة (مراجعة):
- `NotificationRecipient`: مفهرس على `(teacher_id, notification_id)` — جيد.
- `Report`: مفهرس على `(school_id, teacher_id, created_at)` — جيد.
- `AuditLog`: مفهرس على `(timestamp)` — يكفي لـ cleanup.
- `SchoolMembership`: مفهرس على `(teacher_id, school_id)` — جيد للبحث.

---

## 7) Celery & Queue Scaling Report

### الوضع الحالي:

| الطابور | المهام | الحجم المتوقع/يوم عند 1000 مدرسة |
|---------|-------|-----------------------------------|
| `default` | عام | ~100 |
| `notifications` | إشعارات + إيميل | ~5,000-15,000 |
| `images` | معالجة صور | ~25,000 |
| `periodic` | ملخص يومي + تذكيرات | ~4 (لكن كل واحدة O(N)) |

### مخاطر Queue Starvation:

| السيناريو | المخاطرة | الحل |
|-----------|----------|------|
| bulk report upload → image queue flood | عالية | `rate_limit="30/m"` ✅ موجود |
| Global notification → 25K recipients | متوسطة | Single task مع batch — يحتاج fan-out |
| Daily summary → 1000 school iterations | عالية | يحتاج fan-out عند >500 |

### خطة فصل Workers:

```
المرحلة الحالية:  1 worker → -Q default,notifications,images,periodic
500 مدرسة:       2 workers → worker-1: -Q default,notifications
                              worker-2: -Q images,periodic
1000 مدرسة:      3+ workers → worker-notifications: -Q notifications
                               worker-images: -Q images
                               worker-periodic: -Q periodic
                               worker-default: -Q default
```

### Chunking/Fan-out Thresholds:

| المهمة | الحد | الإجراء المطلوب |
|--------|------|-----------------|
| `send_daily_manager_summary_task` | >500 مدرسة | Fan-out: مهمة أب تُطلق subtask لكل مدرسة |
| `check_subscription_expiry_task` | >5000 اشتراك | Chunking بـ 500 سجل |
| `send_notification_task` (global) | >10K مستلم | Fan-out per-school |
| `remind_unsigned_circulars_task` | >1000 تعميم | Chunking (حالياً مقيّد بنافذة زمنية) |

---

## 8) Load Testing Readiness Report

### السيناريوهات الجاهزة للاختبار:

الأداة الموجودة: `check_live_perf.py` — تدعم concurrent requests مع login/session.

| السيناريو | المسار | الأولوية | ملاحظات |
|-----------|--------|----------|---------|
| Login flow | `/login/` POST | عالية | أثقل عملية (bcrypt + session create) |
| Dashboard | `/` | عالية | nav_context + أثقل context processor |
| Reports list | `/reports/` | عالية | Paginated queryset + nav_context |
| Report create | POST `/reports/create/` | متوسطة | DB write + image task dispatch |
| Notifications | `/notifications/` | متوسطة | NotificationRecipient JOIN |
| API - reports | `/api/reports/` | متوسطة | DRF serialization |
| WebSocket connect | `ws://*/ws/notifications/` | متوسطة | يحتاج أداة WS (wscat/artillery) |
| Periodic task impact | `op_diagnostics` command | منخفضة | قياس عبر timing metrics |

### خطوات اختبار الحمل المقترحة:

```bash
# 1. Warmup (2 requests)
python check_live_perf.py --base-url http://localhost:8000 \
  --requests 2 --concurrency 1 --warmup 2

# 2. Baseline (24 requests, 6 concurrent)
python check_live_perf.py --base-url http://localhost:8000 \
  --requests 24 --concurrency 6 --warmup 2 \
  --out-json baseline.json

# 3. Stress test (100 requests, 20 concurrent)
python check_live_perf.py --base-url http://localhost:8000 \
  --requests 100 --concurrency 20 --warmup 2 \
  --out-json stress.json

# 4. Compare results
python check_live_perf.py --base-url http://localhost:8000 \
  --requests 24 --concurrency 6 --warmup 2 \
  --before-json baseline.json --out-json after.json
```

### أدوات مقترحة للمراحل المتقدمة:
- **Locust** (Python) — لسيناريوهات مستخدمين واقعية.
- **k6** — لاختبارات حمل عالية التزامن.
- **Artillery** — لاختبارات WebSocket.
- هذه **توصيات فقط** — لم تُضف كاعتماديات.

---

## 9) SLA/SLO/Alerting Readiness Report

### SLO المقترحة (أولية):

| المقياس | الهدف | الحد الحرج | كيف يُقاس |
|---------|-------|------------|-----------|
| **Availability** | ≥99.5% شهرياً | <99% | `healthz` responses / total |
| **HTTP p95 response** | ≤500ms | >1000ms | `check_live_perf.py` أو monitoring |
| **HTTP p99 response** | ≤1500ms | >3000ms | Same |
| **Celery queue lag** | ≤60s median | >300s | `celery inspect active` |
| **WS abnormal close rate** | ≤5% | >15% | `opmetrics: ws.close.abnormal` |
| **Redis memory** | ≤80% of plan | >90% | `redis INFO memory` |
| **DB query latency p95** | ≤50ms | >200ms | DB monitoring |
| **Error rate (5xx)** | ≤0.5% | >2% | Log aggregation |

### مؤشرات الصحة الحرجة:

| المؤشر | المصدر | التنبيه |
|--------|--------|--------|
| `/healthz/` returns 503 | Health check endpoint | فوري |
| Worker not responding | `celery inspect ping` | فوري |
| Redis `connected_clients` = 0 | Redis INFO | فوري |
| DB connection failures | Django logs | فوري |
| `opmetrics: celery.periodic.*.count` = 0 (missed run) | `/ops/metrics/` | خلال ساعة |
| Audit log cleanup didn't run | `cleanup_audit_logs_task` | خلال يوم |

### قنوات التنبيه المقترحة:
- **Render health checks** — مدمجة (تستخدم `/healthz/`).
- **UptimeRobot / BetterStack** — مراقبة خارجية لـ uptime.
- **Cloudflare analytics** — error rates, response times.
- هذه **توصيات** — لم تُنفذ كأدوات.

---

## 10) Cost Model Report

### Drivers التكلفة (مرتبة بالأثر):

| المكوّن | ما يرفع التكلفة | أول ما يحتاج ترقية |
|---------|-----------------|---------------------|
| **Web instances** | عدد المتزامنين | عند 500 مدرسة |
| **Database** | حجم البيانات + الاتصالات | عند 500 مدرسة (connection pooling) |
| **Redis** | الذاكرة + الاتصالات | عند 500 مدرسة (cache split) |
| **Celery workers** | عدد المهام/دقيقة | عند 500 مدرسة (queue split) |
| **Storage (R2)** | الصور والوسائط | خطي — تكلفة منخفضة |
| **Bandwidth** | Cloudflare CDN يمتص معظمه | تكلفة ثابتة تقريبًا |

### Milestones التوسع:

#### المرحلة الحالية (<100 مدرسة)
- 1 web instance, 1 worker, 1 beat
- Redis starter plan
- PostgreSQL أساسي
- **لا تغييرات مطلوبة**

#### 100-500 مدرسة
- **Web**: 1-2 instances
- **Worker**: 1-2 (بدء فصل image queue)
- **Redis**: ترقية من starter إلى standard (أو فصل cache)
- **DB**: إضافة PgBouncer أو connection pooling
- **تكلفة تقديرية**: +50-100% من الوضع الحالي

#### 500-1000 مدرسة
- **Web**: 2-3 instances
- **Worker**: 3+ (worker per queue type)
- **Redis**: 2-3 instances منفصلة
- **DB**: PostgreSQL مُدار مع read replica
- **Celery**: Fan-out للمهام الدورية
- **تكلفة تقديرية**: 3-5× الوضع الحالي

#### 1000+ مدرسة
- **Web**: 3+ instances مع autoscaling
- **Worker**: dedicated worker fleet
- **Redis**: Redis Cluster أو instances متعددة
- **DB**: Read replica + archiving strategy
- **تكلفة تقديرية**: 5-10× الوضع الحالي

---

## 11) Deployment / Rollback Safety Report

### الوضع الحالي:

| البند | الحالة | ملاحظات |
|-------|--------|---------|
| Health check | ✅ `/healthz/` | يفحص DB + Cache + Channels |
| Zero-downtime deploy | ✅ Render يدعم rolling deploy | بشرط health check pass |
| Startup sequence | ⚠️ `migrate + collectstatic` أولاً | يعمل — لكن migrations طويلة تؤخر startup |
| Rollback | ✅ Render يدعم redeploy to previous commit | يدوي عبر Dashboard |
| Config rollback | ⚠️ Environment variables لا تُحفظ في git | يحتاج توثيق داخلي |
| Beat singleton | ✅ Instance واحد | لا يمكن تكراره |
| Migration safety | ⚠️ بعض migrations قد تقفل جداول كبيرة | يحتاج مراجعة قبل deploy |

### ملاحظات تشغيلية:
1. **`GUNICORN_MAX_REQUESTS=2000`** — يعيد تشغيل workers دوريًا لمنع memory leaks. جيد.
2. **`CELERY_MAX_TASKS_PER_CHILD=200`** — يعيد تشغيل worker processes. جيد.
3. **`CONN_MAX_AGE=600`** — يبقي اتصالات DB مفتوحة 10 دقائق. جيد مع instance واحد، يحتاج pooler مع عدة instances.
4. **Secret regeneration**: Render يُولّد `SECRET_KEY` تلقائيًا — لكن تغييره يبطل كل الجلسات. يجب عدم تغييره.

---

## 12) Remaining Gaps

### فجوات تحتاج قرارًا معماريًا (لم تُنفذ):

| # | الفجوة | الأولوية | متى تُعالج |
|---|--------|----------|-----------|
| 1 | **Fan-out للملخص اليومي** — `send_daily_manager_summary_task` يمر على كل المدارس | P0 | عند 500 مدرسة |
| 2 | **Connection pooler (PgBouncer)** — يحتاج إعداد خارجي | P1 | عند 2+ web instances |
| 3 | **فصل Redis instances** — cache/broker/channels | P1 | عند >200MB memory |
| 4 | **فصل Celery workers** — worker per queue | P1 | عند 500 مدرسة |
| 5 | **Cache stampede protection** — nav_context عند cache miss متزامن | P2 | عند 1000+ مستخدم متزامن |
| 6 | **Notification fan-out per school** — global notification مع 25K مستلم | P2 | عند 500 مدرسة |
| 7 | **Read replica** للاستعلامات القرائية | P2 | عند DB CPU >70% |
| 8 | **NotificationRecipient archiving** — تنظيف الصفوف القديمة | P3 | عند >10M صف |
| 9 | **nav_context cache TTL increase** — من 20s إلى 60s | P3 | قرار UX |
| 10 | **External monitoring** (UptimeRobot/DataDog/Sentry) | P2 | قرار تشغيلي |

### المرحلة السادسة المقترحة:
**"Horizontal Scale-Out Execution"** — تنفيذ فعلي لـ:
1. Fan-out الملخص اليومي
2. فصل workers
3. إضافة web instance ثانية
4. PgBouncer
5. External monitoring setup

---

## 13) Acceptance Criteria Checklist

- [x] لا تغيير في business logic
- [x] لا كسر في الصلاحيات أو العزل بين المستأجرين
- [x] readiness للتوسع أصبحت أوضح (capacity model + milestones)
- [x] capacity model واضح ومبني على فرضيات صريحة
- [x] التشغيل والمراقبة أوضح (SLO/SLA + alerting thresholds)
- [x] التعديلات منخفضة المخاطر (middleware optimization + throttle + docs)
- [x] كل شيء سهل المراجعة في git diff
- [x] `manage.py check` — 0 issues
- [x] `manage.py test` — 97/97 OK
- [x] خطة تكلفة/نمو واضحة
- [x] load testing scenarios موثقة
- [x] deployment safety موثقة
- [x] الفجوات المتبقية محددة بوضوح مع أولويات
