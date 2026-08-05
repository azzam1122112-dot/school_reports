# خطة إزالة «مشرف المنصة» و«مشرف التقارير»

خطة تنفيذية دقيقة، مبنية على جرد فعلي للكود (لا تقدير). تُنفَّذ بالترتيب، وكل مرحلة
قابلة للتسليم والاختبار وحدها.

---

## القرار المعتمد

**«مشرف المنصة» = حسابات المشرفين المفوَّضين فقط.** لوحة إدارة المنصة وصفحاتها الـ38
تبقى كاملة، لكن تصير للمالك (`is_superuser`) وحده. لا يُحذف أي شيء من: المدفوعات،
الاشتراكات، الباقات، الشكاوى، سجل العمليات، الإعدادات، دليل المدارس، محتوى منصور.

**«مشرف التقارير» = `SchoolMembership.RoleType.REPORT_VIEWER`** (عضوية مدرسية للعرض فقط).

**البيانات:** حذف عضويات `report_viewer` نهائيًا، وحذف حسابات مشرفي المنصة المفوَّضين.
**شرط سلامة إلزامي: لا يُحذف حساب `is_superuser` أبدًا مهما كانت حالته.**

---

## الجرد الفعلي

| الملف | مواضع | الملف | مواضع |
|---|---|---|---|
| `views/achievements.py` | 18 | `views/api.py` | 8 |
| `forms.py` | 16 | `views/teachers.py` | 6 |
| `middleware.py` | 15 | `views/_helpers.py` | 6 |
| `templates/base.html` | 14 | `views/tickets.py` | 4 |
| `views/reports.py` | 12 | `views/mansour.py` | 4 |
| `views/platform.py` | 12 | `context_processors.py` | 4 |
| `views/auth.py` | 11 | `tasks.py` · `api_views.py` | 3 لكلٍّ |
| `views/notifications.py` | 10 | `mansour_*.py` | 4 |

إضافة إلى: `model_parts/schools.py`، `admin.py`، `permissions.py`، `urls.py`،
4 قوالب أخرى، و3 وحدات اختبار. **الإجمالي ≈ 180 موضعًا في 29 ملفًا.**

---

## المرحلة ١ — المساعد الذكي (منفصلة، بلا مخاطر أمنية)

- `mansour_knowledge.py`: حذف `AUDIENCE_PLATFORM_SUPERVISOR` و`AUDIENCE_REPORT_SUPERVISOR`
  من `AUDIENCE_LABELS` و`PUBLIC_AUDIENCES` و`_FALLBACK_ROLE_GUIDANCE` و`_FALLBACK_ROLE_DEFAULT_SLUGS`.
- `mansour_knowledge_content.json`: حذف البندين `report-supervisor-read-only` و
  `platform-supervisor-scope`، وإزالة الجمهورين من `audiences` في بقية البنود
  (`report-supervisor`، `platform-supervisor`، `platform-communication`، `manager-reports`).
- `mansour_assistant.py`: حذف الفروع في `_role_overview_reply` و`_offline_customer_reply`
  و`_page_context_preferred_slug`، وتبسيط `AUDIENCE_SUPERVISOR` (لم يعد يحتاج التفريق
  بين نوعي المشرف — يُحذف أيضًا إن لم يعد له معنى).
- `mansour_quality.py`: حذف `report_supervisor` و`platform_supervisor` من
  `ROLE_FORBIDDEN_CAPABILITIES`.
- `mansour_eval_cases.json` و`mansour_quality_cases.json`: حذف الحالات ذات الجمهورين
  (`report-supervisor-scope`، `platform-supervisor-scope`، وما يماثلها).
- `views/mansour.py`: `_resolve_audience` — حذف فرعي المشرفين.
- **تحقق:** `evaluate_mansour` و`evaluate_mansour_quality` و`test_mansour_*`.

---

## المرحلة ٢ — النماذج والصلاحيات (نُفِّذت ونُقِّحت مرة، جاهزة للإعادة)

### `model_parts/schools.py`
1. حذف الحقل `Teacher.is_platform_admin`.
2. حذف السطر الذي يذكره من docstring الخاص بـ `display_role_label`.
3. حذف الصنفين `PlatformAdminRole` و`PlatformAdminScope` بالكامل
   (من `class PlatformAdminRole` حتى تعليق «تعليقات خاصة»).
4. حذف `REPORT_VIEWER` من `SchoolMembership.RoleType`.
5. حذف كتلة «حد أقصى لمشرفي التقارير: 2 نشطين» في `save()`
   (حتى `return super().save(*args, **kwargs)`).

### `admin.py`
حذف `PlatformAdminScope` و`PlatformAdminRole` من الاستيراد، وحذف الصنفين
`PlatformAdminScopeAdmin` و`PlatformAdminRoleAdmin`.

### `permissions.py`
- حذف من `__all__`: `is_report_viewer_for_school`، `is_platform_admin`، `platform_can_access_school`.
- حذف الدوال الثلاث بالكامل.
- `platform_allowed_schools_qs` تصير:
  ```python
  if not getattr(user, "is_superuser", False):
      return School.objects.none()
  return School.objects.filter(is_active=True)
  ```
- `effective_user_role_label`: حذف فرع `is_platform_admin` وفرع `is_report_viewer_for_school`.
- `_get_report_permission_scope`: حذف مفتاح `is_platform_admin` وشرطه.
- `allowed_categories_for`: حذف فرعي المشرفين.
- دالة تصفية التقارير: حذف كتلة «مشرف عام: رؤية مقيدة».

### الترحيل
```python
def purge(apps, schema_editor):
    SchoolMembership = apps.get_model("reports", "SchoolMembership")
    Teacher = apps.get_model("reports", "Teacher")
    SchoolMembership.objects.filter(role_type="report_viewer").delete()
    # لا يُحذف مالك النظام أبدًا
    Teacher.objects.filter(is_platform_admin=True, is_superuser=False).delete()
```
ثم `RemoveField` للحقل، و`DeleteModel` للصنفين. `reverse_code=migrations.RunPython.noop`
مع ملاحظة صريحة أن الحذف غير قابل للتراجع.

---

## المرحلة ٣ — الحراسات والواجهات (الأكبر)

**القاعدة الموحَّدة:** كل `is_superuser or is_platform_admin(user)` تصير `is_superuser`.
وكل `platform_can_access_school(...)` تُحذف مع الفرع الحاوي لها.

- `views/platform.py`: `_require_platform_admin_or_superuser` → `is_superuser` فقط،
  و`_require_platform_school_access` تُبسَّط. حذف
  `platform_admins_list` / `platform_admin_create` / `platform_admin_update` / `platform_admin_delete`.
- `views/achievements.py`: حذف `report_viewer_create/update/toggle/delete` (4 دوال)
  وفروع `_is_report_viewer`.
- `views/_helpers.py`: حذف `_is_report_viewer` والاستيرادات الثلاثة.
- `views/auth.py`, `home.py`, `api.py`, `notifications.py`, `reports.py`, `tickets.py`,
  `teachers.py`, `api_views.py`: تطبيق القاعدة الموحَّدة.
- `middleware.py`: حذف `PlatformAdminAccessMiddleware` بالكامل ومن `MIDDLEWARE` في
  `config/settings.py`، وحذف كتلة `report_viewer` للقراءة فقط
  (`_is_report_viewer` و`report_viewer_read_only` و`report_viewer_forbidden`).
- `forms.py`: حذف `PlatformAdminCreateForm` وكل فروع `is_platform` في نماذج الإشعارات
  والتعاميم.
- `context_processors.py`: حذف `IS_REPORT_VIEWER` و`"p" if is_platform_admin` من مفتاح الكاش.
- `tasks.py`: حذف `report_viewer` من قائمة الأدوار وفرعي `is_platform_admin=True`.
- `urls.py`: حذف مسارات `platform_admins_list` و`platform_admin_*` و`report_viewer_*`.
- القوالب: `base.html` (14)، `manage_teachers.html`، `notifications_sent.html`،
  `send_circular.html`، `platform_schools_directory.html`.

---

## المرحلة ٤ — الاختبارات والتحقق

- `tests/test_platform_admin_experience.py`: حذف/تعديل حالات المشرفين المفوَّضين.
- `tests/test_mansour_assistant.py`: حذف حالات الجمهورين (14 موضعًا).
- `tests/test_teacher_journey_audit.py`: `MANAGER_ONLY_PAGES` قد تحتاج تحديثًا.
- اختبار جديد يثبّت النتيجة: **لا وجود لأي من الاسمين في الكود**، وأن back office
  مغلقة على غير `is_superuser`.
- `python manage.py test reports core` كاملة (~680 اختبارًا، ~30 دقيقة).

---

## ترتيب التنفيذ الموصى به

١ (المساعد) → ٢ (النماذج والصلاحيات) → ٣ (الحراسات) → ٤ (الاختبارات).

بين المرحلتين ٢ و٣ **يكون المستودع غير قابل للتشغيل** — لأن الصلاحيات حُذفت وما زال
هناك من يستدعيها. لذلك يجب تنفيذ ٢ و٣ في **دفعة واحدة متصلة**، ولا تُترك الجلسة بينهما.
`python manage.py check` هو مؤشر الاكتمال: لا يمر إلا بعد انتهاء المرحلة ٣ كاملة.
