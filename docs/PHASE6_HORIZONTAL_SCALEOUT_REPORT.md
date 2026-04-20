# المرحلة 6: التوسع الأفقي — تقرير التنفيذ
# Phase 6: Horizontal Scale-Out Execution Report

**التاريخ**: يوليو 2025  
**النتيجة**: 98/98 اختبار ناجح ✅ | `manage.py check` بدون أخطاء ✅

---

## 1. ملخص تنفيذي

تم تنفيذ 8 مراحل فرعية (A–H) لجعل المنصة جاهزة للتوسع الأفقي الكامل.
كل تغيير قابل للتراجع (rollback-safe) مع الاحتفاظ بالتوافق العكسي.

---

## 2. المرحلة A — جاهزية التوسع الأفقي للويب

**النتيجة**: التطبيق **stateless بالكامل** — آمن للتشغيل على عدة نسخ ويب.

| المكون | الحالة | ملاحظات |
|--------|--------|---------|
| Sessions | Redis-backed | `SESSION_ENGINE = django.contrib.sessions.backends.cache` |
| Cache | Redis (django_redis) | مشترك بين جميع النسخ |
| Media | R2 (S3-compatible) | لا يعتمد على الملفات المحلية في الإنتاج |
| Channels | Redis backend | WebSocket groups عبر Redis |
| `_thread_locals` | Per-request only | يُنظف عند نهاية كل طلب |
| Sticky sessions | **غير مطلوبة** | — |

**Rollback**: لا يوجد تغيير في الكود — تحليل فقط.

---

## 3. المرحلة B — فصل عمال Celery

**التغيير**: تم تقسيم worker واحد إلى 4 عمال متخصصين.

### Procfile (قبل → بعد)
```
# قبل:
worker: celery -A config worker ... -Q default,notifications,images,periodic

# بعد:
worker_default:       ... -Q default        (concurrency=2)
worker_notifications: ... -Q notifications  (concurrency=2)
worker_images:        ... -Q images         (concurrency=1)
worker_periodic:      ... -Q periodic       (concurrency=1)
```

### render.yaml
- تم استبدال `school-worker` بـ 4 خدمات: `school-worker-default`, `school-worker-notifications`, `school-worker-images`, `school-worker-periodic`
- كل خدمة لها نفس متغيرات البيئة (ENV, DATABASE_URL, REDIS_URL, R2_*, إلخ)
- الأمر القديم محفوظ كتعليق في Procfile للتراجع السريع

**Rollback**: أعد الخدمة الواحدة في render.yaml + أزل التعليق عن السطر القديم في Procfile.

---

## 4. المرحلة C — Fan-out للتقرير اليومي

**التغيير**: تم تقسيم `send_daily_manager_summary_task` إلى:
1. **Dispatcher** (`send_daily_manager_summary_task`): يجلب قائمة المدارس النشطة ويرسل subtask لكل مدرسة
2. **Per-school worker** (`_daily_summary_for_school`): يعالج مدرسة واحدة (إشعارات + بريد + واتساب)

### الفوائد:
- كل مدرسة تُعالج بشكل مستقل بحدود وقت خاصة (60s soft / 120s hard)
- فشل مدرسة واحدة لا يؤثر على البقية
- يمكن معالجة مئات المدارس بالتوازي عبر عدة workers

### التوجيه:
```python
"reports.tasks._daily_summary_for_school": {"queue": "periodic"},
```

### الاختبارات:
- 3 اختبارات موجودة تم تحديثها لاستدعاء `_daily_summary_for_school` مباشرة
- اختبار جديد `test_daily_dispatcher_fans_out_per_school` يتحقق من أن الـ dispatcher يستدعي `.delay()` لكل مدرسة

**Rollback**: أعد الدالة القديمة (inline loop) من git history.

---

## 5. المرحلة D — جاهزية PgBouncer

**التغيير**: تم إضافة ملاحظات تفصيلية في `config/settings.py` (قسم Database).

### الإعدادات الموصى بها:
| الإعداد | القيمة | السبب |
|---------|--------|-------|
| `pool_mode` | `transaction` | Django يستخدم SET/RESET |
| `default_pool_size` | `20` | حسب max_connections |
| `max_client_conn` | `200` | يستوعب جميع العمال |
| `server_idle_timeout` | `300` | نصف CONN_MAX_AGE |

### متى تفعيله:
- عند 500+ مدرسة نشطة
- عند ظهور "too many connections" في PostgreSQL logs

**Rollback**: أعد DATABASE_URL للاتصال المباشر بـ PostgreSQL.

---

## 6. المرحلة E — فصل حمل Redis

**التغيير**: توثيق خريطة المفاتيح في `config/settings.py`.

| البادئة | DB | الغرض |
|---------|-----|-------|
| `sr:*` | 1 | Django cache |
| `celery*` | 0 | Celery broker |
| `asgi:*` | 0 | Channels (WebSocket) |
| `opmetrics:*` | 1 | عدادات التشغيل |

### متى الفصل:
- ذاكرة Cache > 200 MB → Redis منفصل لـ cache
- طوابير Celery > 1000 لأكثر من 5 دقائق → Redis منفصل لـ broker
- اتصالات WS > 5000 → Redis منفصل لـ channels

**Rollback**: لا يوجد تغيير في الكود — توثيق فقط.

---

## 7. المرحلة F — تعزيز المراقبة (`/ops/metrics/`)

**التغيير**: تم توسيع `ops_metrics()` في `core/views.py` لإضافة:

| المقياس | المصدر | الوصف |
|---------|--------|-------|
| `db_vendor` | Django connection | نوع قاعدة البيانات |
| `db_conn_max_age` | settings | عمر الاتصال |
| `redis_cache_keys` | Redis INFO keyspace | عدد المفاتيح الكلي |
| `redis_used_memory_mb` | Redis INFO memory | الذاكرة المستخدمة (MB) |
| `queue_len_*` | Redis LLEN | طول كل طابور Celery |

### مثال الاستجابة:
```json
{
  "bucket": "2025071012",
  "metrics": {"celery.periodic.daily_manager_summary.count": 1},
  "infra": {
    "db_vendor": "postgresql",
    "redis_cache_keys": 342,
    "redis_used_memory_mb": 12.3,
    "queue_len_default": 0,
    "queue_len_notifications": 2,
    "queue_len_images": 0,
    "queue_len_periodic": 1
  }
}
```

**Rollback**: أعد الدالة القديمة (snapshot فقط) من git history.

---

## 8. المرحلة G — أمان النشر

### healthz محسّن:
- أُضيف `instance` field يظهر `RENDER_INSTANCE_ID` أو `HOSTNAME` — يميّز النسخ عند التوسع

### ترتيب النشر الآمن:
1. **انشر العمال أولاً** (worker_default, worker_notifications, worker_images, worker_periodic)
2. تأكد أن العمال جاهزة (Render health checks)
3. **انشر beat** (إن تغيّر)
4. **انشر الويب** أخيراً
5. تأكد من `/healthz/` لكل نسخة ويب

### التراجع:
- Render: اضغط "Rollback" على أي خدمة من Dashboard
- Procfile fallback: أزل التعليق عن السطر القديم `worker:` وعلّق العمال الأربعة

---

## 9. المرحلة H — اختبار الحمل

**التغيير**: أمر إدارة جديد `load_test`.

### الاستخدام:
```bash
python manage.py load_test --username admin --password pass --users 50 --requests-per-user 4
python manage.py load_test --base-url https://app.tawtheeq-ksa.com --username admin --password pass --users 100
```

### الميزات:
- إنشاء جلسات مصادقة (CSRF-safe)
- محاكاة 50-100 مستخدم متزامن
- ضرب 5 مسارات شائعة (/home/, /reports/admin/, /staff/departments/, /reports/my/, /notifications/mine/)
- إخراج: min/p50/p95/max latency, throughput (req/s), error rate

### مقارنة مع check_live_perf.py:
| الأداة | الغرض |
|--------|-------|
| `check_live_perf.py` | قياس أداء مستمر مع JSON output + delta مقارنة |
| `load_test` command | محاكاة حمل عالي (50-100 مستخدم) لاختبار السعة |

---

## 10. الملفات المعدّلة

| الملف | التغيير |
|-------|---------|
| `Procfile` | 4 عمال بدل 1 + fallback معلّق |
| `render.yaml` | 4 خدمات worker بدل 1 |
| `reports/tasks.py` | fan-out: dispatcher + per-school subtask |
| `config/settings.py` | routing للـ subtask + PgBouncer notes + Redis prefix map |
| `core/views.py` | ops_metrics مع infra stats + healthz instance ID |
| `reports/tests.py` | 4 اختبارات (3 محدّثة + 1 جديد) |
| `reports/management/commands/load_test.py` | أمر جديد |

---

## 11. نتائج الاختبارات

```
Found 98 test(s).
Ran 98 tests in 100.416s
OK
```

- **97 اختبار سابق**: جميعها ناجحة بدون تعديل (باستثناء 3 اختبارات daily summary تم تحديثها)
- **1 اختبار جديد**: `test_daily_dispatcher_fans_out_per_school`
- **`manage.py check`**: بدون أخطاء

---

## 12. خريطة التوسع المقترحة

| المدارس | الإجراء |
|---------|---------|
| 1-100 | الوضع الحالي (3 web, 4 workers, 1 beat, 1 Redis) |
| 100-500 | أضف web instance ثاني (WEB_CONCURRENCY=3 × 2 = 6 workers) |
| 500-1000 | فعّل PgBouncer + أضف worker instance لكل queue حسب الحاجة |
| 1000+ | Redis منفصل لـ cache vs broker + read replica لـ PostgreSQL |

---

## 13. التوصيات القادمة

1. **مراقبة Queue Lengths**: أضف تنبيه عندما يتجاوز أي طابور 100 مهمة لأكثر من 5 دقائق
2. **Auto-scaling على Render**: فعّل auto-scaling للـ web و worker_default عند زيادة الحمل
3. **Prometheus/Grafana**: عند الحاجة لمراقبة أعمق، أضف django-prometheus
4. **Database Indexing**: راجع أداء الاستعلامات الأكثر تكراراً عند تجاوز 500 مدرسة
5. **Rate Limiting per School**: أضف throttle على مستوى المدرسة لمنع إساءة الاستخدام
