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

ربط ميسر مؤجل إلى يوم الربط المتفق عليه. لا يُفعّل استقبال دفعات حقيقية قبل اختبار الإنشاء، الرجوع، webhook، التوقيع، منع التكرار، الاسترداد، وتسوية المبلغ في بيئة مزود الدفع.
