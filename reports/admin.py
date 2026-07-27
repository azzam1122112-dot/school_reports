# reports/admin.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from django import forms
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from django.utils import timezone
from django.utils.html import format_html

from .forms import DepartmentForm  # نموذج القسم الذي يحتوي على reporttypes

from .models import (
    Teacher,
    WebAuthnCredential,
    Department,
    ReportType,
    ReportTemplate,
    AcademicYear,
    Report,
    Ticket,
    TicketNote,
    School,
    SchoolMembership,
    PlatformAdminScope,
    PlatformAdminRole,
    PlatformSettings,
    SubscriptionPlan,
    SchoolSubscription,
    SchoolArchiveAddon,
    SchoolYearArchive,
    SchoolYearArchiveDownload,
    ArchiveStorageOption,
    Payment,
    AuditLog,
)

# =========================
# نماذج إدارة المستخدم المخصص (Teacher)
# =========================
class TeacherCreationForm(forms.ModelForm):
    """
    نموذج إنشاء مستخدم في لوحة الإدارة مع حقلي كلمة مرور.
    ملاحظة: is_staff لا يظهر هنا لأنه يُحدَّث تلقائيًا من الدور.
    """
    password1 = forms.CharField(label="كلمة المرور", widget=forms.PasswordInput)
    password2 = forms.CharField(label="تأكيد كلمة المرور", widget=forms.PasswordInput)

    class Meta:
        model = Teacher
        fields = ("phone", "name", "national_id", "is_active")

    def clean_password2(self):
        p1 = self.cleaned_data.get("password1")
        p2 = self.cleaned_data.get("password2")
        if p1 and p2 and p1 != p2:
            raise forms.ValidationError("كلمتا المرور غير متطابقتين.")
        return p2

    def save(self, commit=True):
        user = super().save(commit=False)
        # تعيين كلمة المرور
        user.set_password(self.cleaned_data["password1"])
        if commit:
            user.save()
        return user


class TeacherChangeForm(forms.ModelForm):
    """
    نموذج تعديل مستخدم في لوحة الإدارة (لا يظهر كلمة المرور الحقيقية).
    is_staff للعرض فقط (read-only) لأنه يُحدَّث تلقائيًا حسب الدور.
    """
    class Meta:
        model = Teacher
        fields = (
            "phone",
            "name",
            "national_id",
            "is_active",
            "is_superuser",
            "groups",
            "user_permissions",
        )


# =========================
# إدارة المعلمين (Teacher)
# =========================
@admin.register(Teacher)
class TeacherAdmin(UserAdmin):
    add_form = TeacherCreationForm
    form = TeacherChangeForm
    model = Teacher

    list_display = ("name", "phone", "national_id", "is_active", "is_staff")
    list_filter = ("is_active", "is_staff", "is_superuser", "groups")
    search_fields = ("name", "phone", "national_id")
    ordering = ("name",)

    fieldsets = (
        (None, {"fields": ("phone", "password")}),
        ("المعلومات الشخصية", {"fields": ("name", "national_id")}),
        (
            "الصلاحيات",
            {
                "fields": (
                    "is_active",
                    "is_staff",       # للعرض فقط
                    "is_superuser",
                    "groups",
                    "user_permissions",
                )
            },
        ),
        ("تواريخ النظام", {"fields": ("last_login",)}),
    )
    readonly_fields = ("last_login", "is_staff")

    add_fieldsets = (
        (
            None,
            {
                "classes": ("wide",),
                "fields": (
                    "phone",
                    "name",
                    "national_id",
                    "password1",
                    "password2",
                    "is_active",
                ),
            },
        ),
    )

    def delete_queryset(self, request, queryset):
        AuditLog.objects.filter(teacher__in=queryset).delete()
        return super().delete_queryset(request, queryset)

    def delete_model(self, request, obj):
        AuditLog.objects.filter(teacher=obj).delete()
        return super().delete_model(request, obj)


@admin.register(WebAuthnCredential)
class WebAuthnCredentialAdmin(admin.ModelAdmin):
    list_display = ("teacher", "device_name", "is_active", "last_used_at", "created_at")
    list_filter = ("is_active", "created_at", "last_used_at")
    search_fields = ("teacher__name", "teacher__phone", "device_name", "credential_id_hash")
    readonly_fields = ("credential_id_hash", "last_used_at", "created_at")


# =========================
# إدارة التصنيفات/الأقسام (ديناميكي)
# =========================
@admin.register(ReportType)
class ReportTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "order", "is_active", "created_at", "updated_at")
    list_filter = ("is_active", "created_at", "updated_at")
    search_fields = ("name", "code", "description")
    list_editable = ("order", "is_active")
    ordering = ("order", "name")
    prepopulated_fields = {"code": ("name",)}


@admin.register(AcademicYear)
class AcademicYearAdmin(admin.ModelAdmin):
    list_display = ("value", "is_active", "order", "created_at")
    list_filter = ("is_active",)
    list_editable = ("is_active", "order")
    search_fields = ("value",)
    ordering = ("-value",)


@admin.register(ReportTemplate)
class ReportTemplateAdmin(admin.ModelAdmin):
    list_display = ("name", "school", "category", "is_active", "order", "updated_at")
    list_filter = ("is_active", "school")
    search_fields = ("name", "title", "idea")
    list_editable = ("is_active", "order")
    autocomplete_fields = ("category",)
    ordering = ("school", "order", "name")


@admin.register(Department)
class DepartmentAdmin(admin.ModelAdmin):
    # ✅ تسجيل واحد فقط للقسم — لا تكرار!
    form = DepartmentForm  # يحتوي على حقل reporttypes
    list_display = ("name", "slug", "role_label", "is_active")
    list_filter = ("is_active",)
    search_fields = ("name", "slug", "role_label")
    prepopulated_fields = {"slug": ("name",)}
    filter_horizontal = ("reporttypes",)  # اختيار متعدد لأنواع التقارير


# =========================
# إدارة التقارير (Report)
# =========================
@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = (
        "title",
        "teacher",
        "category",
        "report_date",
        "day_name",
        "beneficiaries_count",
        "created_at",
        "preview_image1",
    )
    list_filter = ("category", "report_date", "created_at", "teacher")
    search_fields = (
        "title",
        "idea",
        "teacher__name",
        "teacher__phone",
        "teacher__national_id",
        "category__name",
        "category__code",
    )
    date_hierarchy = "report_date"
    autocomplete_fields = ("teacher", "category")
    list_select_related = ("teacher", "category")
    readonly_fields = ("created_at",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            school_ids = SchoolMembership.objects.filter(
                teacher=request.user, is_active=True
            ).values_list("school_id", flat=True)
            qs = qs.filter(school_id__in=school_ids)
        return qs

    def preview_image1(self, obj):
        if getattr(obj, "image1", None):
            url = getattr(getattr(obj, "image1", None), "url", "")
            if url:
                return format_html(
                    '<img src="{}" width="60" height="60" style="object-fit:cover;border-radius:6px;" />',
                    url,
                )
        return "—"

    preview_image1.short_description = "معاينة الصورة"


# =========================
# إدارة التذاكر والملاحظات (Ticket / TicketNote)
# =========================
class TicketNoteInline(admin.TabularInline):
    model = TicketNote
    extra = 0
    fields = ("author", "is_public", "body", "created_at")
    readonly_fields = ("created_at",)
    autocomplete_fields = ("author",)


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "title",
        "is_platform",
        "status",
        "department",
        "creator",
        "assignee",
        "created_at",
        "updated_at",
    )
    list_filter = ("is_platform", "status", "department", "created_at", "updated_at", "assignee")
    search_fields = (
        "id",
        "title",
        "body",
        "creator__name",
        "creator__phone",
        "assignee__name",
        "assignee__phone",
        "department__name",
        "department__slug",
    )
    date_hierarchy = "created_at"
    autocomplete_fields = ("creator", "assignee", "department")
    list_select_related = ("creator", "assignee", "department")
    readonly_fields = ("created_at", "updated_at")
    inlines = (TicketNoteInline,)

    fieldsets = (
        (None, {"fields": ("title", "body", "attachment", "is_platform")}),
        ("الملكية والتعيين", {"fields": ("creator", "assignee", "department")}),
        ("الحالة", {"fields": ("status",)}),
        ("أخرى", {"fields": ("created_at", "updated_at")}),
    )

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            school_ids = SchoolMembership.objects.filter(
                teacher=request.user, is_active=True
            ).values_list("school_id", flat=True)
            qs = qs.filter(school_id__in=school_ids)
        return qs


@admin.register(TicketNote)
class TicketNoteAdmin(admin.ModelAdmin):
    list_display = ("id", "ticket", "author", "is_public", "created_at")
    list_filter = ("is_public", "created_at", "author")
    search_fields = ("ticket__id", "ticket__title", "body", "author__name")
    autocomplete_fields = ("ticket", "author")
    date_hierarchy = "created_at"
    readonly_fields = ("created_at",)


# =========================
# إدارة المدارس وعضوياتها
# =========================
@admin.register(School)
class SchoolAdmin(admin.ModelAdmin):
    list_display = ("name", "code", "current_academic_year", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "code")
    prepopulated_fields = {"code": ("name",)}

    # عرض سجل العمليات الخاصة بهذه المدرسة داخل صفحة المدرسة في Django Admin
    inlines = ()

    def has_delete_permission(self, request, obj=None):
        # Only superusers can delete schools.
        return bool(getattr(request.user, "is_superuser", False))

    def get_actions(self, request):
        actions = super().get_actions(request)
        if not bool(getattr(request.user, "is_superuser", False)):
            actions.pop("delete_selected", None)
        return actions

    def delete_model(self, request, obj):
        from .middleware import set_audit_logging_suppressed

        set_audit_logging_suppressed(True)
        try:
            return super().delete_model(request, obj)
        finally:
            set_audit_logging_suppressed(False)

    def delete_queryset(self, request, queryset):
        from .middleware import set_audit_logging_suppressed

        set_audit_logging_suppressed(True)
        try:
            return super().delete_queryset(request, queryset)
        finally:
            set_audit_logging_suppressed(False)


class AuditLogInline(admin.TabularInline):
    model = AuditLog
    extra = 0
    can_delete = False
    show_change_link = False
    fields = ("timestamp", "teacher", "action", "model_name", "object_repr", "ip_address")
    readonly_fields = fields
    ordering = ("-timestamp",)

    def has_add_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


# ربط الـ inline بعد تعريفه (حتى لا يتطلب ترتيب تعريفات مختلف)
SchoolAdmin.inlines = (AuditLogInline,)


@admin.register(SchoolMembership)
class SchoolMembershipAdmin(admin.ModelAdmin):
    list_display = ("teacher", "school", "role_type", "is_active", "created_at")
    list_filter = ("role_type", "is_active", "school")
    search_fields = ("teacher__name", "teacher__phone", "school__name", "school__code")
    autocomplete_fields = ("teacher", "school")
    list_select_related = ("teacher", "school")


@admin.register(PlatformAdminScope)
class PlatformAdminScopeAdmin(admin.ModelAdmin):
    list_display = ("admin", "role", "gender_scope")
    list_filter = ("role", "gender_scope")
    search_fields = ("admin__name", "admin__phone")
    autocomplete_fields = ("admin", "role")
    filter_horizontal = ("allowed_schools",)


@admin.register(PlatformAdminRole)
class PlatformAdminRoleAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "is_active", "order")
    list_filter = ("is_active",)
    search_fields = ("name", "slug")
    ordering = ("order", "name")



from django.contrib import admin
from .models import Notification, NotificationRecipient  # استورد من موضعك الفعلي

@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "is_important", "created_by", "created_at", "expires_at")
    search_fields = ("title", "message")
    list_filter = ("is_important", "created_at")
    list_select_related = ("created_by",)

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        if not request.user.is_superuser:
            school_ids = SchoolMembership.objects.filter(
                teacher=request.user, is_active=True
            ).values_list("school_id", flat=True)
            qs = qs.filter(school_id__in=school_ids)
        return qs

@admin.register(NotificationRecipient)
class NotificationRecipientAdmin(admin.ModelAdmin):
    list_display = ("id", "notification", "teacher", "is_read", "created_at", "read_at")
    list_filter = ("is_read", "created_at")
    search_fields = ("notification__title", "teacher__name")
    list_select_related = ("notification", "teacher")


# =========================
# إدارة الاشتراكات والمالية
# =========================
@admin.register(SubscriptionPlan)
class SubscriptionPlanAdmin(admin.ModelAdmin):
    list_display = ("name", "price", "days_duration", "max_teachers", "is_active", "created_at")
    list_filter = ("is_active", "created_at")
    search_fields = ("name", "description")
    ordering = ("price",)


@admin.register(SchoolSubscription)
class SchoolSubscriptionAdmin(admin.ModelAdmin):
    list_display = ("school", "plan", "start_date", "end_date", "is_active", "is_expired")
    list_filter = ("is_active", "plan", "start_date", "end_date")
    search_fields = ("school__name", "school__code")
    autocomplete_fields = ("school", "plan")
    date_hierarchy = "start_date"

    def save_model(self, request, obj, form, change):
        """عند إضافة/تحديث اشتراك من Django Admin نسجل عملية مالية تلقائياً.

        الهدف: أي اشتراك يُفعّل يدوياً من الأدمن يجب أن يظهر في صفحة المالية بدون الحاجة لرفع إيصال.
        """
        prev = None
        if change and getattr(obj, "pk", None):
            try:
                prev = SchoolSubscription.objects.filter(pk=obj.pk).only(
                    "is_active",
                    "canceled_at",
                    "cancel_reason",
                    "start_date",
                    "end_date",
                    "plan_id",
                ).first()
            except Exception:
                prev = None

        super().save_model(request, obj, form, change)

        # ✅ إذا تم إلغاء الاشتراك (إلغاء مقصود)، نسجل حدث الإلغاء في المالية وسجل عمليات المدرسة.
        # لا نعتبر is_active=False وحده إلغاءً لأنه قد يُستخدم للإيقاف المؤقت.
        try:
            became_cancelled = bool(getattr(obj, "is_cancelled", False)) and (not bool(getattr(prev, "is_cancelled", False)))
        except Exception:
            became_cancelled = False

        if became_cancelled:
            try:
                today = timezone.localdate()
                reason = (getattr(obj, "cancel_reason", "") or "").strip()
                note = "تم إلغاء الاشتراك بواسطة Django Admin."
                if reason:
                    note = f"{note}\nسبب الإلغاء: {reason}"

                # 1) سجل حدث الإلغاء نفسه
                exists_cancel_event = Payment.objects.filter(
                    subscription=obj,
                    status=Payment.Status.CANCELLED,
                    payment_date=today,
                    amount=0,
                ).exists()
                if not exists_cancel_event:
                    Payment.objects.create(
                        school=obj.school,
                        subscription=obj,
                        requested_plan=obj.plan,
                        amount=0,
                        receipt_image=None,
                        payment_date=today,
                        status=Payment.Status.CANCELLED,
                        notes=note,
                        created_by=getattr(request, "user", None),
                    )

                # 2) إلغاء أي مدفوعات معلّقة تخص فترة الاشتراك (تحصين حتى لا تُعتمد لاحقًا)
                period_start = getattr(obj, "start_date", None)
                pending_qs = Payment.objects.filter(subscription=obj, status=Payment.Status.PENDING)
                if period_start:
                    pending_qs = pending_qs.filter(payment_date__gte=period_start)
                for p in pending_qs.only("id", "status", "notes"):
                    p.status = Payment.Status.CANCELLED
                    p.notes = (f"{p.notes}\n" if (p.notes or "").strip() else "") + note
                    p.save(update_fields=["status", "notes", "updated_at"])
            except Exception:
                # لا نُفشل حفظ الاشتراك بسبب مشكلة تسجيل المالية.
                return

        # لا نسجل دفعات لاشتراك غير نشط أو باقة مجانية.
        try:
            if not bool(getattr(obj, "is_active", False)):
                return
            plan = getattr(obj, "plan", None)
            price = getattr(plan, "price", None)
            if price is None:
                return
            try:
                if float(price) <= 0:
                    return
            except Exception:
                pass

            period_start = getattr(obj, "start_date", None)
            qs = Payment.objects.filter(
                subscription=obj,
                requested_plan=obj.plan,
                status__in=[Payment.Status.PENDING, Payment.Status.APPROVED],
            )
            if period_start:
                qs = qs.filter(created_at__date__gte=period_start)
            if qs.exists():
                return

            today = timezone.localdate()
            Payment.objects.create(
                school=obj.school,
                subscription=obj,
                requested_plan=obj.plan,
                amount=obj.plan.price,
                receipt_image=None,
                payment_date=today,
                status=Payment.Status.APPROVED,
                notes="تم تسجيل الدفعة تلقائياً عند إضافة/تفعيل الاشتراك من Django Admin.",
                created_by=getattr(request, "user", None),
            )
        except Exception:
            # لا نُفشل حفظ الاشتراك بسبب مشكلة تسجيل المالية.
            return


@admin.register(PlatformSettings)
class PlatformSettingsAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "maintenance_mode_enabled",
        "share_link_default_days",
        "archive_addon_annual_price",
        "archive_included_storage_gb",
        "free_storage_mb",
        "updated_at",
    )
    readonly_fields = ("created_at", "updated_at")
    fields = (
        "maintenance_mode_enabled",
        "maintenance_message",
        "share_link_default_days",
        "archive_addon_annual_price",
        "archive_included_storage_gb",
        "free_storage_mb",
        "updated_by",
        "created_at",
        "updated_at",
    )

    def has_add_permission(self, request):
        if PlatformSettings.objects.exists():
            return False
        return super().has_add_permission(request)


@admin.register(SchoolArchiveAddon)
class SchoolArchiveAddonAdmin(admin.ModelAdmin):
    list_display = ("school", "is_enabled", "start_date", "end_date", "storage_limit_gb", "paid_amount", "is_active", "days_remaining")
    list_filter = ("is_enabled", "start_date", "end_date")
    search_fields = ("school__name", "school__code", "notes")
    autocomplete_fields = ("school",)
    date_hierarchy = "start_date"


@admin.register(ArchiveStorageOption)
class ArchiveStorageOptionAdmin(admin.ModelAdmin):
    list_display = ("storage_gb", "price", "is_active", "sort_order", "updated_at")
    list_filter = ("is_active",)
    ordering = ("sort_order", "storage_gb", "id")


@admin.register(SchoolYearArchive)
class SchoolYearArchiveAdmin(admin.ModelAdmin):
    list_display = (
        "school",
        "academic_year",
        "version",
        "status",
        "file_count",
        "ticket_count",
        "circular_count",
        "storage_bytes",
        "created_by",
        "created_at",
    )
    list_filter = ("status", "academic_year", "created_at")
    search_fields = ("school__name", "school__code", "academic_year", "archive_sha256")
    autocomplete_fields = ("school", "created_by")
    readonly_fields = (
        "school",
        "academic_year",
        "version",
        "status",
        "archive_file",
        "storage_bytes",
        "archive_sha256",
        "file_count",
        "missing_file_count",
        "failed_pdf_count",
        "report_count",
        "achievement_count",
        "ticket_count",
        "circular_count",
        "notification_count",
        "notes",
        "created_by",
        "created_at",
    )

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return bool(request.user.is_superuser)

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(SchoolYearArchiveDownload)
class SchoolYearArchiveDownloadAdmin(admin.ModelAdmin):
    list_display = ("archive", "downloaded_by", "downloaded_at")
    list_filter = ("downloaded_at",)
    search_fields = ("archive__school__name", "archive__academic_year", "downloaded_by__name")
    readonly_fields = ("archive", "downloaded_by", "downloaded_at")

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "school",
        "purpose",
        "requested_plan",
        "amount",
        "status",
        "effects_applied_at",
        "payment_date",
        "created_at",
    )
    list_filter = ("purpose", "status", "payment_date", "created_at")
    search_fields = ("school__name", "notes", "transaction_id")
    autocomplete_fields = ("school", "requested_plan")
    date_hierarchy = "created_at"
    readonly_fields = ("effects_applied_at", "created_at")


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    list_display = ("timestamp", "teacher", "action", "model_name", "object_repr", "school", "ip_address")
    list_filter = ("action", "model_name", "timestamp", "school")
    search_fields = ("teacher__name", "object_repr", "ip_address", "changes")
    readonly_fields = ("timestamp", "teacher", "action", "model_name", "object_id", "object_repr", "changes", "ip_address", "user_agent", "school")
    date_hierarchy = "timestamp"

    def get_model_perms(self, request):
        """إظهار الموديل في قائمة Django Admin حتى لو لم تُمنح صلاحية view صراحةً للموظف.

        يظل الوصول فعلياً محكوماً بـ get_queryset (تصفية حسب عضوية المدارس) وبأن الصفحة read-only.
        """
        perms = super().get_model_perms(request)
        user = getattr(request, "user", None)
        if user is None:
            return perms
        if getattr(user, "is_superuser", False):
            return perms
        if getattr(user, "is_staff", False):
            perms["view"] = True
            perms["add"] = False
            perms["change"] = False
            perms["delete"] = False
        return perms

    def has_view_permission(self, request, obj=None):
        user = getattr(request, "user", None)
        return bool(user and getattr(user, "is_staff", False))

    def get_queryset(self, request):
        qs = super().get_queryset(request)
        user = getattr(request, "user", None)
        if user is None:
            return qs.none()
        if getattr(user, "is_superuser", False):
            return qs

        # تقييد سجل العمليات داخل لوحة Django Admin:
        # مدير المدرسة/الموظف يرى فقط سجلات المدارس المرتبط بها عبر العضويات النشطة.
        from django.db.models import Q
        from .models import SchoolMembership

        allowed_school_ids = list(
            SchoolMembership.objects.filter(teacher=user, is_active=True).values_list("school_id", flat=True)
        )
        if not allowed_school_ids:
            # لا توجد عضويات: لا نُظهر سجلات (بدلاً من عرض كل شيء بالخطأ)
            return qs.none()

        qs = qs.filter(school_id__in=allowed_school_ids)

        # لا نعرض سجلات أنشأها مستخدمون خارج المدرسة (مثل السوبر/مشرف المنصة)
        # حتى لو أثّرت على المدرسة، لتجنب خلط السجلات بين المدارس.
        qs = qs.filter(
            Q(teacher__isnull=True)
            | Q(
                teacher__school_memberships__school_id__in=allowed_school_ids,
                teacher__school_memberships__is_active=True,
            )
        ).distinct()
        return qs

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        # فقط السوبر يوزر يمكنه الحذف (اختياري)
        return request.user.is_superuser

