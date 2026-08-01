# reports/urls.py
from django.urls import path
from . import views

app_name = "reports"

urlpatterns = [
    # =========================
    # الدخول والخروج
    # =========================
    path("", views.platform_landing, name="landing"),
    path("assistant/mansour/", views.mansour_assistant_reply, name="mansour_assistant_reply"),
    path("guide/", views.user_guide, name="user_guide"),
    path("guide/download/", views.user_guide_download, name="user_guide_download"),
    path("guide/download/pdf/", views.user_guide_download_pdf, name="user_guide_download_pdf"),
    path("login/", views.login_view, name="login"),
    path("password-reset/", views.AccountPasswordResetView.as_view(), name="password_reset"),
    path("password-reset/done/", views.AccountPasswordResetDoneView.as_view(), name="password_reset_done"),
    path(
        "password-reset/confirm/<uidb64>/<token>/",
        views.AccountPasswordResetConfirmView.as_view(),
        name="password_reset_confirm",
    ),
    path(
        "password-reset/complete/",
        views.AccountPasswordResetCompleteView.as_view(),
        name="password_reset_complete",
    ),
    path("platform-login/", views.login_view, {"admin_only": True}, name="platform_login"),
    path("login/passkey/options/", views.passkey_login_options, name="passkey_login_options"),
    path("login/passkey/verify/", views.passkey_login_verify, name="passkey_login_verify"),
    path("register/", views.register_school, name="register_school"),
    path("register/success/", views.registration_success, name="registration_success"),
    path("logout/", views.logout_view, name="logout"),
    path("profile/", views.my_profile, name="my_profile"),
    path("profile/passkey/register/options/", views.passkey_register_options, name="passkey_register_options"),
    path("profile/passkey/register/verify/", views.passkey_register_verify, name="passkey_register_verify"),
    path("profile/passkey/prompt/dismiss/", views.passkey_enroll_prompt_dismiss, name="passkey_enroll_prompt_dismiss"),

    # =========================
    # الصفحة الرئيسية
    # =========================
    path("home/", views.home, name="home"),

    # =========================
    # التقارير (للمعلّم)
    # =========================
    path("reports/add/", views.add_report, name="add_report"),
    path("reports/ai/improve/", views.improve_report_text, name="improve_report_text"),
    path("reports/my/", views.my_reports, name="my_reports"),
    path("reports/<int:pk>/edit/", views.edit_my_report, name="edit_my_report"),
    path("reports/<int:pk>/delete/", views.delete_my_report, name="delete_my_report"),

    # الطباعة والتصدير
    path("reports/<int:pk>/print/", views.report_print, name="report_print"),

    # مشاركة التقرير (اختياري للمعلم)
    path("reports/<int:pk>/share/", views.report_share_manage, name="report_share_manage"),

    # =========================
    # تقارير الإدارة (Staff/Manager)
    # =========================
    path("reports/admin/", views.admin_reports, name="admin_reports"),
    path("reports/admin/<int:pk>/delete/", views.admin_delete_report, name="admin_delete_report"),
    path("archive/", views.school_archive, name="school_archive"),
    path("archive/create/", views.school_archive_create, name="school_archive_create"),
    path("archive/download/<int:pk>/", views.school_archive_download, name="school_archive_download"),
    path("archive/export/", views.school_archive_export, name="school_archive_export"),

    # =========================
    # تقارير المدرسة (مشرف عرض فقط)
    # =========================
    path("reports/school/", views.school_reports_readonly, name="school_reports_readonly"),

    # =========================
    # ملف إنجاز المعلّم
    # =========================
    path("achievement/my/", views.achievement_my_files, name="achievement_my_files"),
    path("achievement/school/", views.achievement_school_files, name="achievement_school_files"),
    path("achievement/school/teachers/", views.achievement_school_teachers, name="achievement_school_teachers"),
    path("achievement/<int:pk>/", views.achievement_file_detail, name="achievement_file_detail"),
    path("achievement/<int:pk>/delete/", views.achievement_file_delete, name="achievement_file_delete"),
    path("achievement/<int:pk>/update-year/", views.achievement_file_update_year, name="achievement_file_update_year"),
    path("achievement/<int:pk>/print/", views.achievement_file_print, name="achievement_file_print"),
    path("achievement/<int:pk>/pdf/", views.achievement_file_pdf, name="achievement_file_pdf"),
    path("achievement/<int:pk>/report-picker/", views.achievement_report_picker, name="achievement_report_picker"),

    # ملف الأداء القيادي لمدير/مديرة المدرسة
    path("leadership-portfolio/", views.leadership_portfolio_list, name="leadership_portfolio_list"),
    path("leadership-portfolio/<int:pk>/", views.leadership_portfolio_detail, name="leadership_portfolio_detail"),
    path("leadership-portfolio/<int:pk>/print/", views.leadership_portfolio_print, name="leadership_portfolio_print"),
    path("leadership-portfolio/<int:pk>/pdf/", views.leadership_portfolio_pdf, name="leadership_portfolio_pdf"),

    # مشاركة ملف الإنجاز (اختياري للمعلم)
    path("achievement/<int:pk>/share/", views.achievement_share_manage, name="achievement_share_manage"),

    # مشاركة عامة عبر token
    path("share/<str:token>/", views.share_public, name="share_public"),
    path("share/<str:token>/report-image/<int:slot>/", views.share_report_image, name="share_report_image"),
    path("share/<str:token>/achievement-pdf/", views.share_achievement_pdf, name="share_achievement_pdf"),

    # =========================
    # إدارة المعلّمين (للمدير)
    # =========================
    path("staff/teachers/", views.manage_teachers, name="manage_teachers"),
    path("staff/teachers/add/", views.add_teacher, name="add_teacher"),
    path("staff/teachers/import/", views.teacher_onboarding, name="bulk_import_teachers"),
    path("staff/teachers/import/template/", views.bulk_import_teachers_template, name="bulk_import_teachers_template"),
    path("staff/teachers/import/issues/", views.bulk_import_teachers_issues, name="bulk_import_teachers_issues"),
    path("staff/teachers/import/result/", views.bulk_import_teachers_result, name="bulk_import_teachers_result"),
    path("staff/teachers/<int:pk>/edit/", views.edit_teacher, name="edit_teacher"),
    path("staff/teachers/<int:pk>/delete/", views.delete_teacher, name="delete_teacher"),

    # =========================
    # إدارة الأقسام + التكليف
    # (اعتمدنا slug:code، ووفّرنا aliases للأسماء/المسارات القديمة)
    # =========================
    path("staff/departments/", views.departments_list, name="departments_list"),

    # إضافة قسم
    path("staff/departments/add/", views.department_create, name="department_create"),

    # تعديل بالأكواد الدلالية (slug/code)
    path("staff/departments/<slug:code>/edit/", views.department_edit, name="department_edit"),

    # الأعضاء بالأكواد الدلالية
    path("staff/departments/<slug:code>/members/", views.department_members, name="department_members"),

    # حذف بالأكواد الدلالية
    path("staff/departments/<slug:code>/delete/", views.department_delete, name="department_delete"),

    # =========================
    # لوحة المدير
    # =========================
    path("staff/select-school/", views.select_school, name="select_school"),
    path("staff/switch-school/", views.switch_school, name="switch_school"),
    path("staff/my-school/", views.school_settings, name="school_settings"),
    path("staff/schools/", views.schools_admin_list, name="schools_admin_list"),
    path("staff/schools/add/", views.school_create, name="school_create"),
    path("staff/schools/<int:pk>/profile/", views.school_profile, name="school_profile"),
    path("staff/schools/<int:pk>/edit/", views.school_update, name="school_update"),
    path("staff/schools/<int:pk>/delete/", views.school_delete, name="school_delete"),
    path("staff/schools/managers/", views.school_managers_list, name="school_managers_list"),
    path("staff/schools/managers/<int:pk>/edit/", views.school_manager_update, name="school_manager_update"),
    path("staff/schools/managers/<int:pk>/delete/", views.school_manager_delete, name="school_manager_delete"),
    path("staff/schools/managers/add/", views.school_manager_create, name="school_manager_create"),
    path("staff/schools/<int:pk>/managers/", views.school_managers_manage, name="school_managers_manage"),
    path("platform/audit-logs/", views.platform_audit_logs, name="platform_audit_logs"),
    path("platform-dashboard/", views.platform_admin_dashboard, name="platform_admin_dashboard"),

    # =========================
    # المشرف العام (عرض + تواصل فقط)
    # =========================
    path("platform/schools/", views.platform_schools_directory, name="platform_schools_directory"),
    path("platform/schools/<int:pk>/enter/", views.platform_enter_school, name="platform_enter_school"),
    path("platform/school/", views.platform_school_dashboard, name="platform_school_dashboard"),
    path("platform/school/reports/", views.platform_school_reports, name="platform_school_reports"),
    path("platform/school/tickets/", views.platform_school_tickets, name="platform_school_tickets"),
    path("platform/school/notify/", views.platform_school_notify, name="platform_school_notify"),
    path("platform/admins/add/", views.platform_admin_create, name="platform_admin_create"),

    # =========================
    # إدارة المشرفين (مدير النظام فقط)
    # =========================
    path("platform/admins/", views.platform_admins_list, name="platform_admins_list"),
    path("platform/admins/<int:pk>/edit/", views.platform_admin_update, name="platform_admin_update"),
    path("platform/admins/<int:pk>/delete/", views.platform_admin_delete, name="platform_admin_delete"),

    path("admin-dashboard/", views.admin_dashboard, name="admin_dashboard"),
    path("staff/audit-logs/", views.school_audit_logs, name="school_audit_logs"),

    # =========================
    # أنواع التقارير
    # =========================
    path("staff/report-types/", views.reporttypes_list, name="reporttypes_list"),
    path("staff/report-types/add/", views.reporttype_create, name="reporttype_create"),
    path("staff/report-types/<int:pk>/edit/", views.reporttype_update, name="reporttype_update"),
    path("staff/report-types/<int:pk>/delete/", views.reporttype_delete, name="reporttype_delete"),

    # =========================
    # قوالب التقارير الجاهزة (مدير المدرسة)
    # =========================
    path("staff/report-templates/", views.report_templates_list, name="report_templates_list"),
    path("staff/report-templates/add/", views.report_template_create, name="report_template_create"),
    path("staff/report-templates/<int:pk>/edit/", views.report_template_update, name="report_template_update"),
    path("staff/report-templates/<int:pk>/delete/", views.report_template_delete, name="report_template_delete"),
    path("api/report-templates/", views.api_report_templates, name="api_report_templates"),

    # =========================
    # تصدير بيانات المدرسة (مدير المدرسة)
    # =========================
    path("staff/export/", views.school_data_export, name="school_data_export"),
    path("staff/export/download/", views.school_data_export_download, name="school_data_export_download"),
    path("staff/export/download/zip/", views.school_data_export_zip, name="school_data_export_zip"),

    # =========================
    # التذاكر (Requests/Tickets)
    # =========================
    path("requests/new/", views.request_create, name="request_create"),
    path("requests/mine/", views.my_requests, name="my_requests"),
    path("requests/school/", views.manager_school_tickets, name="manager_school_tickets"),
    path("requests/inbox/", views.tickets_inbox, name="tickets_inbox"),
    path("requests/assigned/", views.assigned_to_me, name="assigned_to_me"),
    path("requests/<int:pk>/", views.ticket_detail, name="ticket_detail"),
    path("requests/<int:pk>/print/", views.ticket_print, name="ticket_print"),
    path("requests/notes/<int:pk>/edit/", views.ticket_note_edit, name="ticket_note_edit"),
    path("requests/admin/<int:pk>/", views.admin_request_update, name="admin_request_update"),

    # الدعم الفني للمنصة
    path("support/new/", views.support_ticket_create, name="support_ticket_create"),
    path("support/mine/", views.my_support_tickets, name="my_support_tickets"),

    # Officer
    path("officer/reports/", views.officer_reports, name="officer_reports"),
    path("officer/reports/<int:pk>/delete/", views.officer_delete_report, name="officer_delete_report"),

    # Department member (read-only)
    path("department/reports/", views.department_reports, name="department_reports"),

    # =========================
    # API
    # =========================
    path("api/department-members/", views.api_department_members, name="api_department_members"),
    path("api/notification-teachers/", views.api_notification_teachers, name="api_notification_teachers"),
    path("api/school-departments/", views.api_school_departments, name="api_school_departments"),
    path("api/dashboard/school/", views.admin_dashboard_data, name="api_admin_dashboard_data"),
    path("api/dashboard/platform/", views.platform_admin_dashboard_data, name="api_platform_dashboard_data"),
    path("api/dashboard/platform/search/", views.platform_admin_dashboard_search, name="api_platform_dashboard_search"),

    # =========================
    # الإشعارات
    # =========================
    path("notifications/unread-count/", views.unread_notifications_count, name="unread_notifications_count"),
    path("notifications/<int:pk>/", views.notification_detail, name="notification_detail"),
    path("notifications/<int:pk>/delete/", views.notification_delete, name="notification_delete"),
    path("notifications/send/", views.send_notification, name="send_notification"),  # تحويل للإنشاء (توافق قديم)
    # إشعارات (تنبيه/رسالة)
    path(
        "notifications/create/",
        views.notifications_create,
        {"mode": "notification"},
        name="notifications_create",
    ),
    path(
        "notifications/sent/",
        views.notifications_sent,
        {"mode": "notification"},
        name="notifications_sent",
    ),

    # تعاميم (قد تتطلب توقيع وتتبع)
    path(
        "circulars/create/",
        views.notifications_create,
        {"mode": "circular"},
        name="circulars_create",
    ),
    path(
        "circulars/sent/",
        views.notifications_sent,
        {"mode": "circular"},
        name="circulars_sent",
    ),
    path("notifications/mine/", views.my_notifications, name="my_notifications"),
    path("circulars/mine/", views.my_circulars, name="my_circulars"),
    path("notifications/mine/<int:pk>/", views.my_notification_detail, name="my_notification_detail"),
    path("circulars/mine/<int:pk>/", views.my_notification_detail, name="my_circular_detail"),
    path("notifications/mine/<int:pk>/sign/", views.notification_sign, name="notification_sign"),
    path("circulars/mine/<int:pk>/sign/", views.notification_sign, name="circular_sign"),
    path("notifications/<int:pk>/read/", views.notification_mark_read, name="notification_mark_read"),
    path("notifications/mark-all-read/", views.notifications_mark_all_read, name="notifications_mark_all_read"),
    path("circulars/mark-all-read/", views.circulars_mark_all_read, name="circulars_mark_all_read"),
    # جديد: تعليم كمقروء بالاعتماد على رقم الإشعار (للهيرو/الواجهة)
    path(
        "notifications/<int:pk>/read-by-notification/",
        views.notification_mark_read_by_notification,
        name="notification_mark_read_by_notification",
    ),

    # تقارير التواقيع للتعاميم (للمدير/المسؤول)
    path(
        "notifications/<int:pk>/signatures/print/",
        views.notification_signatures_print,
        name="notification_signatures_print",
    ),
    path(
        "notifications/<int:pk>/signatures.csv",
        views.notification_signatures_csv,
        name="notification_signatures_csv",
    ),

    # =========================
    # الاشتراكات والمالية
    # =========================
    path("subscription/expired/", views.subscription_expired, name="subscription_expired"),
    path("subscription/my/", views.my_subscription, name="my_subscription"),
    path("subscription/history/", views.subscription_history, name="subscription_history"),
    path("subscription/payment/create/", views.payment_create, name="payment_create"),
    path("subscription/payment/tamara/", views.tamara_checkout_create, name="tamara_checkout_create"),
    path(
        "subscription/payment/tamara/<int:payment_id>/cancel/",
        views.tamara_checkout_cancel,
        name="tamara_checkout_cancel",
    ),
    path("subscription/payment/tamara/return/<str:result>/", views.tamara_return, name="tamara_return"),
    path("payments/tamara/webhook/", views.tamara_webhook, name="tamara_webhook"),

    # =========================
    # إدارة المنصة (Custom Views)
    # =========================
    path("platform/subscriptions/", views.platform_subscriptions_list, name="platform_subscriptions_list"),
    path("platform/settings/", views.platform_settings, name="platform_settings"),
    path("platform/mansour-content/", views.platform_mansour_content, name="platform_mansour_content"),
    path("platform/academic-years/", views.platform_academic_years, name="platform_academic_years"),
    path("platform/subscriptions/add/", views.platform_subscription_form, name="platform_subscription_add"),
    path("platform/subscriptions/<int:pk>/", views.platform_subscription_detail, name="platform_subscription_detail"),
    path("platform/subscriptions/<int:pk>/renew/", views.platform_subscription_renew, name="platform_subscription_renew"),
    path(
        "platform/subscriptions/<int:pk>/record-payment/",
        views.platform_subscription_record_payment,
        name="platform_subscription_record_payment",
    ),
    path("platform/subscriptions/<int:pk>/delete/", views.platform_subscription_delete, name="platform_subscription_delete"),
    path("platform/plans/", views.platform_plans_list, name="platform_plans_list"),
    path("platform/plans/add/", views.platform_plan_form, name="platform_plan_add"),
    path("platform/plans/<int:pk>/edit/", views.platform_plan_form, name="platform_plan_edit"),
    path("platform/plans/<int:pk>/delete/", views.platform_plan_delete, name="platform_plan_delete"),
    path("platform/payments/", views.platform_payments_list, name="platform_payments_list"),
    path("platform/payments/<int:pk>/", views.platform_payment_detail, name="platform_payment_detail"),
    path("platform/tickets/", views.platform_tickets_list, name="platform_tickets_list"),
    path(
        "platform/complaints/",
        views.platform_complaints_list,
        name="platform_complaints_list",
    ),
    path(
        "platform/complaints/<int:pk>/",
        views.platform_complaint_detail,
        name="platform_complaint_detail",
    ),
    path("platform/archive-addons/", views.platform_archive_addons_list, name="platform_archive_addons_list"),
    path("platform/archive-addons/add/", views.platform_archive_addon_form, name="platform_archive_addon_add"),
    path("platform/archive-addons/<int:pk>/edit/", views.platform_archive_addon_form, name="platform_archive_addon_edit"),
    path("platform/archive-addons/<int:pk>/toggle/", views.platform_archive_addon_toggle, name="platform_archive_addon_toggle"),

    # =========================
    # صفحات المحتوى (Footer)
    # =========================
    path("faq/", views.faq, name="faq"),
    path("privacy/", views.privacy_policy, name="privacy_policy"),
    path("terms/", views.terms_conditions, name="terms_conditions"),
    path("refund-policy/", views.refund_policy, name="refund_policy"),
    path(
        "service-delivery/",
        views.service_delivery_policy,
        name="service_delivery_policy",
    ),
    path("complaints/", views.complaints_policy, name="complaints_policy"),
]
