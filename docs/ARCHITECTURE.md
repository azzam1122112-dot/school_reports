# معمارية منصة توثيق

## تدفق الطلب

1. يصل HTTP إلى تطبيق ASGI في `config/asgi.py`.
2. يمر الطلب عبر middleware الحماية والتتبع والجلسة والمدرسة النشطة.
3. تعرض `reports/views/` واجهة المجال المطلوبة.
4. تستعمل العروض خدمات المجال في `reports/services_*.py`.
5. تمر كل استعلامات البيانات المدرسية ضمن نطاق `School`.
6. تنقل العمليات الثقيلة إلى Celery، وتصل تحديثات العدادات عبر Channels.

## حدود المجالات

| المجال | النماذج | العروض والخدمات |
|---|---|---|
| الهوية والمدارس | `model_parts/schools.py` | `views/auth.py`, `views/schools.py` |
| التقارير | `model_parts/reports.py` | `views/reports.py`, `services_reports.py` |
| ملفات الإنجاز | `model_parts/achievements.py` | `views/achievements.py`, `services_achievement.py` |
| التذاكر | `model_parts/tickets.py` | `views/tickets.py` |
| الإشعارات | `model_parts/notifications.py` | `views/notifications.py`, `tasks.py` |
| الاشتراكات | `model_parts/billing.py` | `views/subscriptions.py` |
| الصيانة | `maintenance/models.py` | `maintenance/services.py` |

يظل `reports/models.py` واجهة التوافق الوحيدة التي تستورد منها بقية المنصة.

## ثوابت العزل والصلاحيات

- `SchoolMembership` هو مصدر الدور داخل المدرسة.
- `request.active_school` هو نطاق الطلب المدرسي الحالي.
- لا يجوز قبول `active_school_id` من الجلسة دون تحقق
  `ActiveSchoolGuardMiddleware`.
- مشرف المنصة يخضع لـ`PlatformAdminScope`.
- كل QuerySet جديد يجب أن يضيف مرشح المدرسة قبل الفلاتر والبحث والترقيم.
- روابط المشاركة العامة تمر عبر `ShareLink` منتهي الصلاحية، وليس رابط الملف
  الدائم.

## الملفات والخصوصية

- R2 خاص افتراضيًا.
- `MEDIA_PUBLIC_ACCESS_ENABLED=False` يفرض روابط موقعة حتى لو بقي إعداد قديم
  `AWS_QUERYSTRING_AUTH=0`.
- لا يستخدم النطاق العام إلا عند تفعيل الوصول العام صراحة.
- أسماء التخزين التاريخية مثل `PublicRawMediaStorage` باقية لتوافق الهجرات ولا
  تعني أن الملفات عامة.

## الكاش والمهام

- Redis DB 0: broker وChannels.
- Redis DB 1: كاش Django والجلسات والأقفال.
- الطوابير: `default`, `notifications`, `images`, `periodic`.
- يجب حماية المهام الدورية بقفل قصير، وجعل المهام قابلة لإعادة التنفيذ بأمان.

## قواعد التطوير

- ضع منطق الأعمال القابل لإعادة الاستخدام في خدمة، لا في القالب.
- لا تضف JavaScript داخليًا دون CSP nonce.
- لا تستخدم `|safe` لبيانات المستخدم؛ استخدم `json_script` أو JSON موثوقًا.
- أضف اختبار عزل لكل مسار يقرأ بيانات مدرسة أو مستخدم.
- شغّل CI المحلي الموضح في `README.md` قبل الدمج.
