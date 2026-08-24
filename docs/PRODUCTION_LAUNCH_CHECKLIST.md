# قائمة الإطلاق التجاري

هذه القائمة هي بوابة إطلاق إلزامية. لا يُنشر الإصدار التجاري إذا فشل أي بند مطلوب.

## قبل النشر

- إنشاء ملف `deploy/hetzner/env.production` بصلاحية `0600` وتعبئة القيم الحقيقية؛ يمنع وضع الأسرار في Git.
- ضبط R2 خاص للوسائط (`MEDIA_PUBLIC_ACCESS_ENABLED=False` و`AWS_QUERYSTRING_AUTH=True`). التطبيق يرفض الإقلاع الإنتاجي من دون تخزين دائم خاص.
- ضبط Resend عبر `reports.email_backends.ResendEmailBackend` ومفتاح
  `RESEND_API_KEY` وعنوان إرسال موثّق، أو SMTP إنتاجي حقيقي. التطبيق يرفض
  `localhost` وواجهات console/locmem في الإنتاج الصارم.
- تفعيل `PASSWORD_CHANGE_EMAIL_ENABLED=True` و
  `SUBSCRIPTION_ACTIVATION_EMAIL_ENABLED=True` و
  `SUBSCRIPTION_EXPIRY_REMINDER_EMAIL_ENABLED=True`، ثم تشغيل فحص الجاهزية
  ورسالة تحقق حقيقية إلى صندوق يراقبه فريق التشغيل:

  ```bash
  python manage.py production_preflight
  python manage.py send_system_email_probe operator@example.com
  ```

  قبول المزود للرسالة يثبت سلامة مسار الإرسال، ثم يجب تأكيد وصولها من الصندوق
  أو من حدث `email.delivered` في Resend.
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

بوابة الدفع الإلكتروني هي **ميسر** وحدها، وإلى جانبها التحويل البنكي. ولا يجوز
أن يظهر في الواجهة اسمُ أو شعارُ وسيلة دفع لا تقبلها المنصة فعلاً — يحرس ذلك
`reports/tests/test_payment_brand_marks.py`.

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

لا يكفي وصول العميل إلى صفحة النجاح؛ التفعيل يعتمد حصريًا على التحقق من الفاتورة
لدى البوابة وتحصيلها كاملاً.
