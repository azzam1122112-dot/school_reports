# صلاحيات مشرف المنصة (المشرف العام) — جدول ومسارات

هذا المستند يوضح صلاحيات **مشرف المنصة** (Platform Admin) داخل النظام، وكيف يتم ضبط الوصول (مسموح/ممنوع) عبر طبقات متعددة (Defense-in-depth).

> ملاحظة مهمة: السوبر يوزر (Superuser) غير مقيد بالـallowlist في الـmiddleware.

---

## التعريفات المختصرة

- **مشرف المنصة**: مستخدم يحقق `user.is_platform_admin == True`.
- **Scope / النطاق**: كائن مرتبط بالمستخدم `user.platform_scope` يحدد المدارس المسموح بها.
  - في [reports/permissions.py](../reports/permissions.py): `platform_allowed_schools_qs` تعمل **Fail-closed**: إذا لم يوجد scope تُرجع `none()`.
- **المدرسة النشطة**: `active_school_id` داخل الـsession ويتم ضبطها عند دخول المدرسة من الدليل.

---

## طبقات الحماية (Defense-in-depth)

1) **Middleware allowlist**
- المصدر: [reports/middleware.py](../reports/middleware.py) داخل `PlatformAdminAccessMiddleware`.
- الفكرة: مشرف المنصة لا يستطيع الوصول إلا لمسارات (URL names) محددة. أي شيء غير ذلك يُمنع (403 أو redirect).

2) **Checks داخل الـviews/APIs**
- المصدر: [reports/views.py](../reports/views.py) + [reports/permissions.py](../reports/permissions.py).
- الفكرة: حتى لو أُضيف مسار للـallowlist بالخطأ، ما زال يجب أن يحترم النطاق عبر `platform_can_access_school` وفلترة المدارس.

---

## المسارات المسموحة لمشرف المنصة (بحسب allowlist)

> العمود “يتطلب مدرسة نشطة؟” يعني عادةً وجود `active_school_id` في session عبر دخول المدرسة من الدليل.

| URL name | المسار (تقريبي) | الطرق | يتطلب Scope؟ | يتطلب مدرسة نشطة؟ | ملاحظات مهمة |
|---|---|---:|---:|---:|---|
| `reports:landing` | `/` | GET | لا | لا | الصفحة العامة؛ المستخدم المسجّل يُحوَّل حسب دوره. |
| `reports:login` | `/login/` | GET/POST | لا | لا | صفحة الدخول. |
| `reports:logout` | `/logout/` | GET/POST | لا | لا | تسجيل الخروج. |
| `reports:my_profile` | `/profile/` | GET/POST | لا | لا | ملف المستخدم. |
| `reports:platform_schools_directory` | `/platform/schools/` | GET | نعم | لا | دليل المدارس ضمن نطاق المشرف. |
| `reports:platform_enter_school` | `/platform/schools/<pk>/enter/` | GET | نعم | لا | يضبط `active_school_id` في session. |
| `reports:platform_school_dashboard` | `/platform/school/` | GET | نعم | نعم | يعرض لوحة المدرسة المختارة. |
| `reports:platform_school_reports` | `/platform/school/reports/` | GET | نعم | نعم | يعرض تقارير المدرسة فقط. |
| `reports:report_print` | `/reports/<pk>/print/` | GET/POST | نعم | اختياري | يسمح بالطباعة إذا كان تقرير المدرسة ضمن النطاق، ويُقيّد أكثر إذا كانت المدرسة النشطة محددة. |
| `reports:platform_school_tickets` | `/platform/school/tickets/` | GET | نعم | نعم | قائمة تذاكر المدرسة (غير platform tickets). |
| `reports:ticket_detail` | `/requests/<pk>/` | GET/POST | نعم | اختياري | تذاكر المدرسة: يجب أن تكون مدرسة التذكرة ضمن النطاق + (إن وُجدت مدرسة نشطة) تطابقها. |
| `reports:platform_school_notify` | `/platform/school/notify/` | GET/POST | نعم | نعم | إرسال إشعار لكل مستخدمي المدرسة. |
| `reports:circulars_create` | `/circulars/create/` | GET/POST | نعم | لا | مشرف المنصة يختار المدرسة من النموذج؛ التحقق يتم داخل الفورم/الـAPI. |
| `reports:circulars_sent` | `/circulars/sent/` | GET | نعم | لا | مشرف المنصة يرى ما أنشأه فقط وضمن نطاقه. |
| `reports:notifications_sent` | `/notifications/sent/` | GET | نعم | لا | نفس منطق “المرسلة” لكن للإشعارات العادية. |
| `reports:notification_detail` | `/notifications/<pk>/` | GET | نعم | غالبًا لا | للتعاميم: يسمح فقط للـcreator ضمن نطاقه. |
| `reports:notification_delete` | `/notifications/<pk>/delete/` | POST | نعم | غالبًا لا | للتعاميم: يسمح فقط للـcreator ضمن نطاقه. |
| `reports:notification_signatures_print` | `/notifications/<pk>/signatures/print/` | GET | نعم | غالبًا لا | تقرير تواقيع (تعاميم فقط): creator + ضمن النطاق. |
| `reports:notification_signatures_csv` | `/notifications/<pk>/signatures.csv` | GET | نعم | غالبًا لا | نفس التقييد السابق بصيغة CSV. |
| `reports:api_notification_teachers` | `/api/notification-teachers/` | GET | نعم | حسب الحالة | يدعم مشرف المنصة: يعتمد على النطاق، وفي وضع circular يتحقق من شروط إضافية. |
| `reports:api_school_departments` | `/api/school-departments/` | GET | نعم | لا (يدعم `target_school`) | مشرف المنصة: يجب تمرير `target_school`/`school` أو أن تكون المدرسة النشطة ضمن النطاق. |
| `reports:api_department_members` | `/api/department-members/` | GET | نعم | لا (يدعم `target_school`) | مشرف المنصة: نفس فكرة `target_school` والتحقق من النطاق. |
| `reports:achievement_school_files` | `/achievement/school/` | GET/POST | نعم | نعم | يتطلب مدرسة نشطة + صلاحية عرض ملف الإنجاز داخل المدرسة (تُفحص في `_can_view_achievement`). |
| `reports:achievement_school_teachers` | `/achievement/school/teachers/` | GET/POST | نعم | نعم | alias يوجّه إلى صفحة ملفات الإنجاز للمدرسة. |
| `reports:achievement_file_detail` | `/achievement/<pk>/` | GET/POST | نعم | نعم | يتحقق أن الملف تابع للمدرسة النشطة، ويسمح لمشرف المنصة إن كانت المدرسة ضمن النطاق. |
| `reports:achievement_file_print` | `/achievement/<pk>/print/` | GET | نعم | نعم | طباعة ملف الإنجاز ضمن نطاق المدرسة. |
| `reports:achievement_file_pdf` | `/achievement/<pk>/pdf/` | GET | نعم | نعم | تصدير PDF ضمن نطاق المدرسة. |
| `reports:unread_notifications_count` | `/notifications/unread-count/` | GET | لا | لا | endpoint بسيط لعرض badge (مسموح لتجنب 403 noisy). |
| `service_worker` | `/sw.js` | GET | لا | لا | مسار خارج namespace reports؛ مذكور في allowlist لدعم PWA. |

---

## ملاحظات خاصة بالـAPIs

- `api_school_departments` و `api_department_members` تدعم مشرف المنصة بطريقتين:
  1) عبر `target_school`/`school` في querystring (مفضل عند عدم وجود مدرسة نشطة).
  2) أو عبر المدرسة النشطة في session بعد الدخول من دليل المدارس.
- التحقق الأساسي لمشرف المنصة يكون عبر `platform_can_access_school`.

---

## عند إضافة مسار جديد لمشرف المنصة

**Checklist سريع**:
1) أضف الـURL name في allowlist داخل `PlatformAdminAccessMiddleware` في [reports/middleware.py](../reports/middleware.py).
2) داخل الـview/الـAPI: أضف تحقق نطاق واضح (يفضل استخدام `platform_can_access_school` أو `platform_allowed_schools_qs`).
3) إن كان المسار يعتمد على مدرسة: حدد هل سيستخدم المدرسة النشطة (session) أم `target_school`.
4) أضف اختبارًا بسيطًا (داخل/خارج النطاق) في [reports/tests.py](../reports/tests.py) لتجنب أي تراجع مستقبلي.
