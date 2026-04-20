# Phase 4: Scalability & Architecture Readiness Report

**تاريخ:** 2025  
**الهدف:** تجهيز المنصة للتوسع من عدد محدود من المدارس إلى آلاف المدارس مع الحفاظ على الأداء والاستقرار والأمان.

---

## 1. ملخص تنفيذي

المرحلة الرابعة ركّزت على البنية التحتية للتوسع: توزيع طوابير Celery، نقاط فحص الصحة، مراقبة الأداء، حماية المهام الدورية من التداخل، وتوثيق المخاطر المعمارية المتبقية. جميع التغييرات آمنة وقابلة للعكس ولا تمسّ منطق الأعمال.

---

## 2. الملفات المعدّلة

| الملف | نوع التغيير |
|-------|-------------|
| `config/settings.py` | إضافة توجيه طوابير Celery (4 طوابير) |
| `reports/tasks.py` | `_periodic_lock`، `rate_limit`، `soft_time_limit`، `opmetrics.timing()`، `.iterator()` |
| `core/views.py` | **ملف جديد** — نقطتا فحص: `/healthz/` و `/ops/metrics/` |
| `core/opmetrics.py` | إضافة دالة `timing()` لقياس مدة المهام |
| `config/urls.py` | إضافة مسارات healthz و ops_metrics |
| `Procfile` | إضافة `-Q default,notifications,images,periodic` للعامل |
| `render.yaml` | إضافة `healthCheckPath` وتحديث أمر العامل |

---

## 3. توجيه الطوابير و Celery

### الطوابير المُعرّفة:
- **`default`** — المهام العامة
- **`notifications`** — الإشعارات والبريد الإلكتروني
- **`images`** — معالجة الصور (`rate_limit="30/m"`)
- **`periodic`** — المهام الدورية (ملخص يومي، تذكيرات، فحص اشتراكات)

### حماية المهام الدورية:
- `_periodic_lock(name, ttl)` يمنع تشغيل نسختين متزامنتين من نفس المهمة عبر `cache.add()`.
- مُطبّق على: `send_daily_manager_summary_task`، `check_subscription_expiry_task`، `remind_unsigned_circulars_task`.

### حدود الوقت:
| المهمة | soft_time_limit | time_limit |
|--------|-----------------|------------|
| `send_notification_task` | 600s | 900s |
| `send_daily_manager_summary_task` | 300s | 600s |
| `check_subscription_expiry_task` | 120s | 300s |
| `remind_unsigned_circulars_task` | 120s | 300s |

---

## 4. الصحة والمراقبة

### `/healthz/` (عام)
يفحص:
- **قاعدة البيانات**: `SELECT 1`
- **ذاكرة التخزين المؤقت (Redis)**: `cache.set` / `cache.get`
- **طبقة القنوات**: اختبار إرسال/استقبال (best-effort)

يعيد `200` إذا نجحت جميع الفحوصات، `503` مع تفاصيل الخلل.

### `/ops/metrics/` (superuser فقط)
يعيد لقطة JSON من مقاييس `opmetrics`:
- عدادات الأحداث (increment)
- مدة تنفيذ المهام الدورية (timing: count + sum_ms)

### `opmetrics.timing(metric, duration_ms)`
تُسجّل مدة التنفيذ لكل مهمة دورية في حاويات بالساعة عبر ذاكرة التخزين المؤقت.

---

## 5. مخاطر وقت التشغيل

| الأولوية | المشكلة | الحالة |
|----------|---------|--------|
| P1 | `send_daily_manager_summary_task` يمرّ على **جميع** المدارس في حلقة واحدة | موثّق — يحتاج fan-out عند >500 مدرسة (يُصدر تحذير الآن) |
| P1 | SubscriptionMiddleware يُنفّذ استعلام SchoolMembership لكل طلب | موثّق — يُعاد استخدام `request.active_school` لكن العضوية تُجلب دائمًا |
| P2 | `School.objects.filter(is_active=True).exists()` مكرر 20+ مرة في views | موثّق — يمكن تجميعه في decorator أو context processor |
| P2 | لا حماية من cache stampede في `cache_utils.py` | موثّق — يمكن إضافة probabilistic early expiry |
| P2 | `School.objects.all()` بدون حد في `views/platform.py` للمشرفين | موثّق — يحتاج pagination |

---

## 6. جاهزية المعالجة الخلفية

### التحسينات المُطبّقة:
- `.iterator()` على استعلامات المدارس/الاشتراكات/التعاميم في المهام الدورية لتقليل استهلاك الذاكرة.
- تحذير تلقائي عند تجاوز 500 مدرسة في الملخص اليومي.
- `rate_limit="30/m"` على معالجة الصور لمنع إغراق العمال.

### المخاطر المتبقية:
- **الملخص اليومي**: حلقة واحدة لجميع المدارس. عند آلاف المدارس، يحتاج تحويل إلى fan-out (مهمة أب تُطلق مهمة فرعية لكل مدرسة).
- **فحص الاشتراكات**: يمرّ على جميع الاشتراكات النشطة. مقبول حتى ~5000 اشتراك ثم يحتاج chunking.
- **تذكير التعاميم**: مقيّد بنافذة زمنية فالحجم محدود طبيعياً.

---

## 7. نظافة الإعدادات

| البند | الحالة |
|-------|--------|
| `SECRET_KEY` في production | ✅ مطلوب من البيئة، يرفض التشغيل بدونه |
| `DEBUG=False` في production | ✅ يُكتشف تلقائياً عبر `RENDER` env |
| `SECURE_SSL_REDIRECT` | ✅ مفعّل في production |
| `SESSION_COOKIE_SECURE/HTTPONLY` | ✅ مفعّل |
| `CSRF_COOKIE_SECURE/HTTPONLY/SAMESITE` | ✅ مفعّل |
| `CONN_MAX_AGE=600` | ✅ يمنع إعادة فتح الاتصال كل طلب |
| `CELERY_TASK_ACKS_LATE=True` | ✅ يضمن عدم فقدان المهام عند تعطل العامل |
| `DATA_UPLOAD_MAX_MEMORY_SIZE=40MB` | ✅ حد معقول |
| Redis separation (DB 0 broker, DB 1 cache) | ✅ |

---

## 8. الفجوات المعمارية المتبقية

1. **Fan-out للمهام الدورية**: الملخص اليومي وفحص الاشتراكات يحتاجان نمط أب/أبناء عند التوسع لآلاف المدارس.
2. **Cache stampede protection**: لا توجد حماية من إعادة بناء التخزين المؤقت المتزامنة. يُوصى بـ probabilistic early expiry أو lock-based refresh.
3. **Unbounded querysets في لوحة المشرف**: `School.objects.all()` في platform views يحتاج pagination.
4. **Redis single-instance**: Broker والتخزين المؤقت والقنوات كلها على نفس مثيل Redis. عند التوسع الكبير، يُوصى بفصلها.
5. **WebSocket scaling**: `InMemoryChannelLayer` في التطوير، ولكن production يستخدم Redis channel layer — جاهز للتوسع.

---

## 9. قائمة معايير القبول

- [x] لا تغييرات في منطق الأعمال
- [x] لا SQL خام
- [x] الصلاحيات والعزل بين المستأجرين محفوظة
- [x] جميع التغييرات قابلة للعكس
- [x] لا تغييرات في سلوك WebSocket
- [x] كل تغيير مبرّر وموثّق
- [x] `/healthz/` يعيد حالة DB + Cache + Channels
- [x] `/ops/metrics/` محمي بـ superuser فقط
- [x] طوابير Celery مُوجّهة (4 طوابير)
- [x] المهام الدورية محمية من التداخل (`_periodic_lock`)
- [x] مدة تنفيذ المهام الدورية مُراقبة (`opmetrics.timing`)
- [x] `.iterator()` على استعلامات الحلقات الكبيرة
- [x] `manage.py check` — بدون أخطاء
- [x] `manage.py test` — 97/97 ناجح
