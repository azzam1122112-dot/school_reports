---
name: dark-mode-audit
description: Audit the platform's dark mode for unreadable text and light surfaces. Use when asked to check, review, or fix dark mode (الوضع الداكن / الليلي), when a page "stays white" or text "disappears" in dark theme, or after adding a template that ships its own <style> block. Runs a real browser over every route and measures computed colours — catches what reading CSS cannot.
---

# فحص الوضع الداكن

الوضع الداكن هنا لا يُكسر بقيمةٍ خاطئة في مكانٍ واحد. يُكسر بـ**تعاقب**:
رمزٌ يُعاد تعريفه داخل نطاق فيحجب القيمة الليلية، أو قاعدةٌ لاحقة تغلب
بالوزن، أو `!important` في ملفٍ آخر. لذلك **قراءة الـCSS لا تكفي** —
والدليل أن مسحاً واحداً في المتصفح كشف ٧٧ عطلاً في ٤٤ صفحة بعد أن كان
الفحص الساكن قد «نظّف» المشروع.

**ابدأ دائماً بالمسح في المتصفح.** الفحوص الساكنة أدواتُ فرزٍ سريع بعده.

## المعمار الذي تفحصه

- `reports/templates/reports/_identity.html` — جذر الهوية. `--id-*` هي
  **وحدها** ما يُعاد تعريفه تحت `data-theme="dark"`. مضمَّن من `base.html`.
- `static/css/dark-mode.css` — طبقة مشتركة تُحمَّل **بعد** `{% block head %}`
  وقبل محتوى الصفحة.
- القالب يعلن رموزه **مقصورةً على حاويته** ومشتقّةً من `--id-*`، فيستغني عن
  كتلةٍ ليلية خاصة. انظر `my_reports.html` نموذجاً.

### ثلاثة مزالق تكرّرت

1. **الحجب.** `.scope { --card-bg:#fff }` يغلب `html[data-theme="dark"]`
   لكل ما تحت `.scope`، فلا تصل القيمة الليلية. يكشفه `shadow_scan.py`.
2. **الشفافية العالية.** `rgba(255,255,255,.96)` سطحٌ مصمت لا زخرفة.
   تجاهُل كل أبيضٍ شفاف يُخفي ألواحاً بيضاء كاملة.
3. **ما يجب أن يبقى فاتحاً.** معاينة التعميم ورقةٌ بيضاء عمداً، وشعارات
   الدفع، وقواعد `@media print`، والأزرار البيضاء فوق ترويسةٍ ملوّنة.
   لا تُعالجها — بل ثبِّت حبرها داكناً.

## التشغيل

```bash
# ١) قاعدة بيانات منفصلة — لا تلمس db.sqlite3
export DB_NAME="$PWD/tmp/darkaudit.sqlite3"
python manage.py migrate --noinput
python .claude/skills/dark-mode-audit/scripts/seed_darkmode.py

# ٢) الخادم. أعِد تشغيله بعد أي تعديل قالب — القوالب تُخزَّن مؤقتاً
DEBUG=True ALLOWED_HOSTS="127.0.0.1,localhost" \
  python manage.py runserver 127.0.0.1:8811 --noreload &

# ٣) المسار ثم المسح
python .claude/skills/dark-mode-audit/scripts/dump_urls.py > tmp/urls.json
cd .claude/skills/dark-mode-audit && npm install --no-audit --no-fund
node scripts/sweep.cjs --urls ../../../tmp/urls.json --base http://127.0.0.1:8811
```

> **أعِد تشغيل الخادم بعد كل تعديل قالب.** ضاعت عليّ ثلاث جولات لأن
> `runserver` كان يقدّم قوالب مخزّنة فبدت الإصلاحات فاشلة. تعديلات
> `.css` تظهر فوراً؛ تعديلات `.html` لا.

يطبع المسح لكل صفحة:
- `BRIGHT` — سطحٌ محسوبٌ فاتح ومساحته تتجاوز 10000px².
- `CONTRAST` — نصّ تباينه دون 4.5:1 (أو 3:1 للكبير)، مع لونه وخلفيته الفعلية.

## الفرز

| التباين | الحكم |
|---|---|
| `BRIGHT` | لوحٌ أبيض — أصلحه، إلا إن كان ورقةً مقصودة |
| `< 2.0` | غير مقروء عملياً — أولوية |
| `2.0 – 3.0` | مقروء بصعوبة، يفشل AA |
| `3.0 – 4.5` | يفشل AA للنص الصغير فقط |

**تحقّق قبل الإصلاح.** المسح يجهل التدرّجات المرسومة فوق الخلفية، ويبلّغ عن
`skip-link` المخفي حتى التركيز. افتح القاعدة واقرأها.

## الإصلاح

رتّب المحاولات هكذا:

1. **الرمز المحلي → `--id-*`** إن كان القالب يملك لوحته. الأنظف: يحذف
   الكتلة الليلية كلها.
2. **قاعدة في `dark-mode.css`** إن كان الصنف مشتركاً بين صفحات، أو إن كانت
   الصفحة لا تُحمّل `_identity.html` (مثل `landing.html` و`user_guide.html`
   — `--id-*` غير معرّفة هناك، فاستعمل قيم اللوحة مباشرة).
3. **في القالب نفسه** إن كانت كتلة `<style>` داخل `{% block content %}`،
   فهي تأتي بعد `dark-mode.css` ولن تصلها التصحيحات المشتركة.

⚠️ **لا تُحوّل اللوحة آلياً.** جرّبته: الخاصية المخصّصة لا تحمل دلالة يُستدل
بها، فصار `--prof-bg` (سطح بطاقة) لونَ حدٍّ شفاف، و`--notif-text` سطحاً
داكناً. الاسم وحده لا يكفي — اقرأ استعمال كل رمز.

⚠️ **احذر اتساع `!important`.** كتبت `.ps-btn { color:… !important }` للنسخة
المصمتة فأصابت الشفافة فصار تباينها 1.04. استعمل `:not()`.

## الفحوص الساكنة (فرزٌ سريع، لا بديل)

```bash
python .claude/skills/dark-mode-audit/scripts/shadow_scan.py   # فخّ الحجب
python .claude/skills/dark-mode-audit/scripts/static_scan.py   # أسطح بلا مقابل ليلي
```

## بعد الإصلاح

```bash
python manage.py test reports.tests.test_dark_mode --settings=config.test_settings
```

`test_dark_mode.py` يثبّت نسخة `dark-mode.css`؛ إن رفعتها في القوالب الثمانية
فارفع الثابت في الاختبار معها. ثم أعد المسح وقارن العدد.
