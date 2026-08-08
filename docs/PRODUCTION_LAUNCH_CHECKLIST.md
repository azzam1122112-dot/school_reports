# قائمة الإطلاق التجاري

هذه القائمة هي بوابة إطلاق إلزامية. لا يُنشر الإصدار التجاري إذا فشل أي بند مطلوب.

## قبل النشر

- إنشاء ملف `deploy/hetzner/env.production` بصلاحية `0600` وتعبئة القيم الحقيقية؛ يمنع وضع الأسرار في Git.
- ضبط R2 خاص للوسائط (`MEDIA_PUBLIC_ACCESS_ENABLED=False` و`AWS_QUERYSTRING_AUTH=True`). التطبيق يرفض الإقلاع الإنتاجي من دون تخزين دائم خاص.
- ضبط SMTP حقيقي واختبار استعادة كلمة المرور. التطبيق يرفض `localhost` وأي backend غير SMTP في الإنتاج الصارم.
- ضبط `SENTRY_DSN` وإعداد تنبيه للأخطاء الجديدة، مع إبقاء `send_default_pii=False`.
- تفعيل مراقب خارجي لـ `/healthz/` وتنبيه عند HTTP 503؛ المسار لا يعرض اسم الخادم أو تفاصيل الاستثناءات.
- ضبط `TRUSTED_PROXY_CIDRS` على شبكة Caddy الداخلية فقط.

## النسخ الاحتياطي

- تفعيل `school-reports-postgres-backup.timer` ومراجعة آخر نسخة في Restic.
- تثبيت rclone وتفعيل `school-reports-media-backup.timer` لنسخ R2 إلى مستودع Restic المشفر خارج الحساب الأساسي.
- إجراء استعادة تجريبية لقاعدة البيانات وملف وسائط واحد قبل أول عميل، ثم شهريًا، وتسجيل زمن الاستعادة.

## بوابة الإصدار

```bash
python -m pip_audit -r requirements.txt
python manage.py check --deploy --fail-level WARNING
python manage.py makemigrations --check --dry-run
python manage.py test
docker compose --env-file deploy/hetzner/env.production -f compose.hetzner.yaml config
```

يجب بناء الصورة من commit/tag معروف، ثم حفظ نتيجة الفحوص وتاريخ النسخة المنشورة.

## الدفع

بوابة الإطلاق هي **ميسر** وحدها. وتمارا مؤجَّلة حتى تصدر موافقتها، ومخفيّة
بالكامل حتى ذلك الحين — اسماً وشعاراً — في صفحة الهبوط وصفحة الاشتراك وسجلّ
المدفوعات وسياسة الخصوصية. يكفي `TAMARA_ENABLED=False` لإخفائها في هذه المواضع
جميعاً، ويحرس ذلك `reports/tests/test_payment_brand_marks.py`.

### ميسر (البوابة المفعَّلة)

- ضبط `MOYASAR_ENABLED=True` و`MOYASAR_ENVIRONMENT=live` ومفتاح `sk_live_*` في
  ملف بيئة الخادم وحده بصلاحية `0600`. الإعدادات ترفض الإقلاع إذا خالف بادئةُ
  المفتاح البيئةَ، وترفض وضع الاختبار في الإنتاج الصارم.
- تسجيل مسار الاستدعاء الراجع عند ميسر:
  `https://tawtheeq-ksa.com/payments/moyasar/callback/<batch_ref>/`.
- تنفيذ عملية إنتاج حقيقية منخفضة القيمة، ثم التحقق من أن الدفع صار `approved`،
  وأن `effects_applied_at` غير فارغ، وأن الباقة والتواريخ فُعِّلت للمدرسة.
- **إعادة إرسال الاستدعاء نفسه** والتأكد من عدم تمديد الاشتراك أو تطبيق أي أثر
  مرتين. التفعيل يعتمد على التحقق من الفاتورة لدى ميسر لا على وصول العميل إلى
  صفحة النجاح.
- اختبار الاسترجاع ومطابقة المبلغ في لوحة ميسر.
- التحقق من أن مهمة `reconcile_pending_gateway_payments_task` تعمل (كل ٢٠ دقيقة)
  وأنها تُنهي الطلبات المعلّقة التي حُصِّلت فعلاً.

### تمارا (مؤجَّلة — لا تُفعَّل ولا يُذكر اسمها)

- اجتياز طلب كامل في Sandbox: إنشاء الطلب، `order_approved`، الاعتماد، التحصيل، ثم `order_captured`.
- تسجيل Webhook من نوع Order على `https://tawtheeq-ksa.com/payments/tamara/webhook/` لأحداث `order_approved` و`order_authorised` و`order_captured` و`order_refunded` و`order_canceled` و`order_declined` و`order_expired`.
- وضع `TAMARA_API_TOKEN` و`TAMARA_NOTIFICATION_TOKEN` في ملف بيئة الخادم فقط، ثم ضبط `TAMARA_ENVIRONMENT=production` و`TAMARA_ENABLED=True` بعد اعتماد الإطلاق من تمارا.
- تنفيذ عملية إنتاج منخفضة القيمة مصرح بها، ثم التحقق من أن الدفع أصبح `approved` وحالة البوابة `fully_captured` وأن `effects_applied_at` غير فارغ والباقة والتواريخ مفعلة للمدرسة.
- إعادة إرسال Webhook نفسه والتأكد من عدم تمديد الاشتراك أو تطبيق أي أثر مرتين، ثم اختبار الاسترجاع والتسوية.

لا يكفي وصول العميل إلى صفحة النجاح؛ التفعيل يعتمد حصريًا على Webhook موثّق وتحصيل كامل من تمارا.

### ميسر

ربط ميسر مؤجل إلى يوم الربط المتفق عليه. لا يُفعّل استقبال دفعات حقيقية قبل اختبار الإنشاء، الرجوع، webhook، التوقيع، منع التكرار، الاسترداد، وتسوية المبلغ في بيئة مزود الدفع.
