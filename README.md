# منصة توثيق

منصة Django عربية متعددة المدارس لإدارة التقارير، الطلبات والتذاكر، التعاميم
والتوقيعات، ملفات الإنجاز، الاشتراكات، المدفوعات، والأرشفة.

## البنية

- `config/`: إعدادات Django وASGI وCelery والمسارات العامة.
- `core/`: فحوص الصحة، المقاييس التشغيلية، والـmiddleware المبكر.
- `reports/`: نطاقات الأعمال الرئيسية والقوالب والـAPI والمهام الخلفية.
- `reports/model_parts/`: النماذج مقسمة حسب المجال مع واجهة توافق في `reports/models.py`.
- `reports/views/`: العروض مقسمة حسب المجال.
- `maintenance/`: المعاينة والتنفيذ الآمن لإعادة تهيئة السنة الدراسية.
- `docs/`: أدلة الاستخدام والتشغيل وتقارير التوسع.

## التشغيل المحلي

يتطلب Python 3.12.

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
Copy-Item .env.example .env
python manage.py migrate
python manage.py runserver
```

SQLite وذاكرة Django المحلية تكفيان للتطوير. اترك متغيرات Redis وR2 فارغة
إذا لم تكن الخدمات متوفرة.

## الاختبارات والفحوص

إعدادات الاختبار تعطل الخدمات الخارجية وتستخدم مجزئ كلمات مرور سريعًا:

```powershell
$env:DJANGO_SETTINGS_MODULE = "config.test_settings"
python manage.py check
python manage.py makemigrations --check --dry-run
ruff check .
python manage.py test
```

ينفذ GitHub Actions الأوامر نفسها لكل طلب دمج ولكل تحديث على `main`.

## PDF على Windows

WeasyPrint يحتاج مكتبات Pango/GObject الأصلية. صورة Docker تثبتها تلقائيًا.
على Windows يجب تثبيت حزمة GTK/Pango المتوافقة وإضافة مجلد المكتبات إلى
`PATH`، أو تشغيل إنشاء PDF داخل Docker.

## خدمات الإنتاج

- PostgreSQL عبر `DATABASE_URL`.
- Redis للكاش والجلسات والقنوات وCelery.
- أربع طوابير Celery: `default`, `notifications`, `images`, `periodic`.
- Cloudflare R2 للوسائط.
- Gunicorn مع Uvicorn worker لتطبيق ASGI.

راجع `.env.example` و`deploy/hetzner/env.production.example` لقائمة المتغيرات كاملة.

### تمارا

تكامل تمارا معطّل افتراضيًا. ابدأ ببيئة `sandbox`، وضع `TAMARA_API_TOKEN`
و`TAMARA_NOTIFICATION_TOKEN` في ملف البيئة الفعلي فقط، ثم سجّل Webhook من نوع
Order على `/payments/tamara/webhook/` لكل أحداث الطلب. لا تنتقل إلى
`TAMARA_ENVIRONMENT=production` قبل اجتياز قائمة اختبار الإطلاق لدى تمارا.

## الأمان

- لا ترفع `.env` أو قاعدة SQLite أو مجلد `media`.
- وسائط R2 خاصة افتراضيًا وتستخدم روابط موقعة منتهية الصلاحية.
- لا تفعل `MEDIA_PUBLIC_ACCESS_ENABLED` إلا بعد مراجعة خصوصية مستقلة.
- عزل المدارس يعتمد `SchoolMembership` والمدرسة النشطة، مع طبقة حماية إضافية
  في middleware.
- أي JavaScript داخلي في القوالب يجب أن يحمل `nonce="{{ CSP_NONCE }}"`.

## قبل أي تغيير

1. حدد المدرسة النشطة في كل استعلام متعدد المستأجرين.
2. استخدم دوال الصلاحيات المشتركة بدل إنشاء قواعد وصول جديدة داخل العرض.
3. أضف اختبار عزل يمنع رؤية بيانات مدرسة أو مستخدم آخر.
4. شغّل الفحوص والاختبارات كاملة قبل النشر.
