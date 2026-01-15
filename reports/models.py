# reports/models.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import timedelta
from typing import Optional
import secrets
import os

from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, FileExtensionValidator
from django.db import models, transaction
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver
from django.utils.text import slugify
from django.utils import timezone

# تخزين المرفقات (R2 أو محلي)
from .storage import PublicRawMediaStorage
from .validators import validate_attachment_file, validate_image_file, validate_pdf_file

# =========================
# ثوابت عامة
# =========================
MANAGER_SLUG = "manager"
MANAGER_NAME = "الإدارة"
MANAGER_ROLE_LABEL = "المدير"


def _normalize_academic_year_hijri(value: str) -> str:
    """تطبيع السنة الدراسية الهجرية بصيغة YYYY-YYYY (مثل 1447-1448)."""
    v = (value or "").strip()
    return v.replace("–", "-").replace("—", "-")


def _validate_academic_year_hijri(value: str) -> None:
    """يتحقق من الصيغة 1447-1448 وأن السنة الثانية = الأولى + 1."""
    import re

    v = _normalize_academic_year_hijri(value)
    if not re.fullmatch(r"\d{4}-\d{4}", v):
        raise ValidationError("صيغة السنة الدراسية يجب أن تكون مثل 1447-1448")
    start, end = v.split("-", 1)
    try:
        s, e = int(start), int(end)
    except Exception:
        raise ValidationError("صيغة السنة الدراسية غير صحيحة")
    if e != s + 1:
        raise ValidationError("السنة الدراسية يجب أن تكون مثل 1447-1448 (فرق سنة واحدة)")


def _achievement_pdf_upload_to(instance: "TeacherAchievementFile", filename: str) -> str:
    year = _normalize_academic_year_hijri(getattr(instance, "academic_year", "")) or "unknown"
    return f"achievements/pdfs/{year}/teacher_{instance.teacher_id}.pdf"


def _achievement_evidence_upload_to(instance: "AchievementEvidenceImage", filename: str) -> str:
    try:
        year = _normalize_academic_year_hijri(instance.section.file.academic_year)
    except Exception:
        year = "unknown"
    return f"achievements/evidence/{year}/section_{instance.section.code}/teacher_{instance.section.file.teacher_id}/{filename}"


def _payment_receipt_upload_to(instance: "Payment", filename: str) -> str:
    """مسار رفع صورة إيصال الدفع"""
    return f"payments/receipts/{filename}"


def _report_image_upload_to(instance: "Report", filename: str) -> str:
    """مسار رفع صور التقرير"""
    return f"reports/{filename}"


def _ticket_attachment_upload_to(instance: "Ticket", filename: str) -> str:
    """مسار رفع مرفقات التذاكر"""
    return f"tickets/attachments/{filename}"


def _school_logo_upload_to(instance: "School", filename: str) -> str:
    """مسار رفع شعار المدرسة"""
    return f"schools/logos/{filename}"


def _ticket_image_upload_to(instance: "TicketImage", filename: str) -> str:
    """مسار رفع صور التذاكر"""
    return f"tickets/images/{filename}"


# =========================
# المدرسة (Tenant)
# =========================
class School(models.Model):
    name = models.CharField("اسم المدرسة", max_length=200)
    class Stage(models.TextChoices):
        KG = "kg", "رياض أطفال"
        PRIMARY = "primary", "ابتدائي"
        MIDDLE = "middle", "متوسط"
        HIGH = "high", "ثانوي"

    class Gender(models.TextChoices):
        BOYS = "boys", "بنين"
        GIRLS = "girls", "بنات"

    code = models.SlugField(
        "المعرّف (code)",
        max_length=64,
        unique=True,
        help_text="كود قصير لتمييز المدرسة، يُستخدم في الاختيار والتقارير.",
    )
    stage = models.CharField(
        "المرحلة",
        max_length=16,
        choices=Stage.choices,
        default=Stage.PRIMARY,
    )
    gender = models.CharField(
        "بنين / بنات",
        max_length=8,
        choices=Gender.choices,
        default=Gender.BOYS,
    )
    phone = models.CharField("رقم الجوال", max_length=20, blank=True, null=True)
    city = models.CharField("المدينة", max_length=120, blank=True, null=True)
    is_active = models.BooleanField("نشطة؟", default=True)
    logo_url = models.URLField("رابط الشعار", blank=True, null=True)
    logo_file = models.ImageField(
        "شعار مرفوع",
        upload_to=_school_logo_upload_to,
        blank=True,
        null=True,
        validators=[validate_image_file],
    )
    print_primary_color = models.CharField(
        "لون قالب الطباعة",
        max_length=9,
        blank=True,
        null=True,
        help_text="لون رئيسي لقالب الطباعة (مثلاً #2563eb).",
    )
    share_link_default_days = models.PositiveSmallIntegerField(
        "مدة صلاحية الروابط الافتراضية (بالأيام)",
        default=7,
        help_text="المدة الافتراضية لروابط مشاركة التقارير/ملفات الإنجاز لهذه المدرسة.",
    )
    created_at = models.DateTimeField("أُنشئت في", auto_now_add=True)
    updated_at = models.DateTimeField("تم التحديث في", auto_now=True)

    class Meta:
        ordering = ("name",)
        verbose_name = "مدرسة"
        verbose_name_plural = "المدارس"

    def __str__(self) -> str:
        return self.name or self.code

    def save(self, *args, **kwargs):
        if self.code:
            self.code = self.code.strip().lower()
        super().save(*args, **kwargs)


# =========================
# مرجع الأدوار الديناميكي
# =========================
class Role(models.Model):
    slug = models.SlugField("المعرّف (slug)", max_length=64, unique=True)
    name = models.CharField("الاسم", max_length=120)

    # يمنح الوصول للوحة التحكم افتراضيًا للمستخدمين الذين يحملون هذا الدور
    is_staff_by_default = models.BooleanField("يمتلك لوحة التحكم افتراضيًا؟", default=False)

    # يرى كل أنواع التقارير (يتجاوز القيود التفصيلية)
    can_view_all_reports = models.BooleanField("يشاهد كل التصنيفات؟", default=False)

    # أنواع التقارير المسموح لهذا الدور برؤيتها (عند تعطيل can_view_all_reports)
    allowed_reporttypes = models.ManyToManyField(
        "ReportType",
        blank=True,
        related_name="roles_allowed",
        verbose_name="الأنواع المسموح بها",
    )

    is_active = models.BooleanField("نشط", default=True)

    class Meta:
        ordering = ("slug",)
        verbose_name = "دور"
        verbose_name_plural = "الأدوار"

    def __str__(self) -> str:
        return self.name or self.slug

    def save(self, *args, **kwargs):
        # تطبيع slug
        if self.slug:
            self.slug = self.slug.strip().lower()
        super().save(*args, **kwargs)


# =========================
# مستخدم النظام: المعلم
# =========================
class TeacherManager(BaseUserManager):
    def create_user(self, phone, name, password=None, **extra_fields):
        if not phone:
            raise ValueError("رقم الجوال مطلوب")
        if not name:
            raise ValueError("اسم المستخدم مطلوب")
        user = self.model(phone=phone.strip(), name=name.strip(), **extra_fields)
        if password:
            user.set_password(password)
        else:
            user.set_unusable_password()
        user.save(using=self._db)
        return user

    def create_superuser(self, phone, name, password=None, **extra_fields):
        extra_fields.setdefault("is_staff", True)
        extra_fields.setdefault("is_superuser", True)
        # إن وُجد دور manager نربطه
        try:
            mgr = Role.objects.filter(slug=MANAGER_SLUG).first()
            if mgr:
                extra_fields.setdefault("role", mgr)
        except Exception:
            pass
        return self.create_user(phone, name, password, **extra_fields)


class Teacher(AbstractBaseUser, PermissionsMixin):
    phone = models.CharField("رقم الجوال", max_length=20, unique=True)
    national_id = models.CharField("الهوية الوطنية", max_length=20, blank=True, null=True, unique=True)
    name = models.CharField("الاسم", max_length=150, db_index=True)

    role = models.ForeignKey(
        Role,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        verbose_name="الدور",
        related_name="users",
    )

    # لاحقاً يمكن ربط المعلّم مباشرة بمدرسة افتراضية
    # school = models.ForeignKey(
    #     School,
    #     on_delete=models.SET_NULL,
    #     null=True,
    #     blank=True,
    #     verbose_name="المدرسة",
    #     related_name="teachers",
    # )

    is_active = models.BooleanField("نشط", default=True)
    # يُحدَّث تلقائيًا حسب role.is_staff_by_default
    is_staff = models.BooleanField("موظّف لوحة", default=False)
    is_platform_admin = models.BooleanField("مشرف عام للمنصة؟", default=False)
    current_session_key = models.CharField(max_length=64, blank=True, default="")
    date_joined = models.DateTimeField("تاريخ الانضمام", auto_now_add=True)

    USERNAME_FIELD = "phone"
    REQUIRED_FIELDS = ["name"]

    objects = TeacherManager()

    class Meta:
        verbose_name = "مستخدم (معلم)"
        verbose_name_plural = "المستخدمون"

    @property
    def role_display(self) -> str:
        return getattr(self.role, "name", "-")

    def save(self, *args, **kwargs):
        try:
            if self.role is not None:
                self.is_staff = bool(self.role.is_staff_by_default)
        except Exception:
            pass
        super().save(*args, **kwargs)

    def __str__(self):
        role_name = getattr(self.role, "name", None)
        if not role_name and getattr(self, "is_platform_admin", False):
            role_name = "مشرف عام"
        return f"{self.name} ({role_name or 'بدون دور'})"


# =========================
# نطاق مشرف عام للمنصة (عرض + تواصل فقط)
# =========================
class PlatformAdminScope(models.Model):
    class GenderScope(models.TextChoices):
        ALL = "all", "الجميع"
        BOYS = "boys", "بنين"
        GIRLS = "girls", "بنات"

    admin = models.OneToOneField(
        Teacher,
        on_delete=models.CASCADE,
        related_name="platform_scope",
        verbose_name="المشرف العام",
    )
    gender_scope = models.CharField(
        "نطاق بنين/بنات",
        max_length=8,
        choices=GenderScope.choices,
        default=GenderScope.ALL,
    )
    allowed_cities = models.JSONField("المدن المسموحة", default=list, blank=True)
    allowed_schools = models.ManyToManyField(
        School,
        blank=True,
        related_name="platform_admins",
        verbose_name="مدارس محددة (اختياري)",
    )

    class Meta:
        verbose_name = "نطاق مشرف عام"
        verbose_name_plural = "نطاقات المشرفين العامين"

    def __str__(self) -> str:
        return f"Scope for {self.admin_id}"


# =========================
# تعليقات خاصة (يراها المعلم فقط)
# =========================
class TeacherPrivateComment(models.Model):
    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="private_comments_received",
        verbose_name="المعلم المستهدف",
    )
    created_by = models.ForeignKey(
        Teacher,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="private_comments_created",
        verbose_name="أضيف بواسطة",
    )
    school = models.ForeignKey(
        School,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="private_comments",
        verbose_name="المدرسة",
    )
    achievement_file = models.ForeignKey(
        "TeacherAchievementFile",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="private_comments",
        verbose_name="ملف الإنجاز (اختياري)",
    )
    report = models.ForeignKey(
        "Report",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="private_comments",
        verbose_name="التقرير (اختياري)",
    )
    body = models.TextField("التعليق")
    created_at = models.DateTimeField("تاريخ الإضافة", default=timezone.now)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "تعليق خاص للمعلم"
        verbose_name_plural = "تعليقات خاصة للمعلمين"

    def __str__(self) -> str:
        return f"PrivateComment#{self.pk} to teacher#{self.teacher_id}"


# =========================
# مرجع الأقسام الديناميكي
# =========================
class Department(models.Model):
    school = models.ForeignKey(
        "School",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="departments",
        verbose_name="المدرسة",
        help_text="يظهر هذا القسم فقط داخل المدرسة المحددة.",
    )
    name = models.CharField("اسم القسم", max_length=120)
    slug = models.SlugField("المعرّف (slug)", max_length=64)
    role_label = models.CharField(
        "الاسم الظاهر في قائمة (الدور)",
        max_length=120,
        blank=True,
        help_text="هذا الاسم سيظهر كخيار (دور) عند إضافة المعلّم. إن تُرك فارغًا سيُستخدم اسم القسم.",
    )
    is_active = models.BooleanField("نشط", default=True)

    # ربط القسم بأنواع التقارير
    reporttypes = models.ManyToManyField(
        "ReportType",
        blank=True,
        related_name="departments",
        verbose_name="أنواع التقارير المرتبطة",
        help_text="اختَر الأنواع التي يحق لمسؤولي هذا القسم الاطلاع عليها (تُزامَن تلقائيًا مع دور القسم).",
    )

    class Meta:
        ordering = ("id",)
        constraints = [
            # ✅ السماح بتكرار slug بين المدارس المختلفة
            models.UniqueConstraint(
                fields=["school", "slug"],
                condition=models.Q(school__isnull=False),
                name="uniq_department_slug_per_school",
            ),
            # ✅ لو وُجدت أقسام عامة (school=NULL) تبقى فريدة عالميًا
            models.UniqueConstraint(
                fields=["slug"],
                condition=models.Q(school__isnull=True),
                name="uniq_global_department_slug",
            ),
        ]
        indexes = [
            models.Index(fields=["school", "slug"]),
        ]
        verbose_name = "قسم"
        verbose_name_plural = "الأقسام"

    def __str__(self):
        return self.name

    # ===== منع حذف قسم المدير الدائم =====
    def delete(self, *args, **kwargs):
        if self.slug == MANAGER_SLUG:
            raise ValidationError("لا يمكن حذف قسم المدير الدائم.")
        return super().delete(*args, **kwargs)

    def save(self, *args, **kwargs):
        """تطبيع slug + فرض خصائص قسم المدير فقط.

        ملاحظة مهمة: كان هناك سابقًا مزامنة تلقائية بين Department.slug و Role.slug.
        هذا لا يعمل مع الأقسام المخصصة لكل مدرسة (لأن Role.slug فريد عالميًا)، لذلك تم إيقافه.
        """
        def _slugify_english(text: str) -> str:
            try:
                from unidecode import unidecode  # type: ignore

                text = unidecode(text or "")
            except Exception:
                pass
            return slugify(text or "", allow_unicode=False)

        if self.slug:
            self.slug = self.slug.strip().lower()
        else:
            self.slug = _slugify_english(self.name or "")

        # fallback: لا نسمح بـ slug فارغ
        if not self.slug:
            self.slug = "dept"

        if self.slug == MANAGER_SLUG:
            self.name = MANAGER_NAME
            self.role_label = MANAGER_ROLE_LABEL
            self.is_active = True

        if not self.role_label:
            self.role_label = self.name

        super().save(*args, **kwargs)


class DepartmentMembership(models.Model):
    TEACHER = "teacher"
    OFFICER = "officer"
    ROLE_TYPE_CHOICES = [(TEACHER, "Teacher"), (OFFICER, "Officer")]

    department = models.ForeignKey(
        Department,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="القسم",
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="dept_memberships",
        verbose_name="المعلم",
    )
    role_type = models.CharField("نوع التكليف", max_length=16, choices=ROLE_TYPE_CHOICES, default=TEACHER)

    class Meta:
        unique_together = [("department", "teacher")]
        indexes = [
            models.Index(fields=["department"]),
            models.Index(fields=["teacher"]),
        ]
        verbose_name = "تكليف قسم"
        verbose_name_plural = "تكليفات الأقسام"

    def __str__(self):
        return f"{self.teacher} @ {self.department} ({self.role_type})"

    # ===== ضمان: قسم المدير يقبل موظفين فقط =====
    def clean(self):
        super().clean()
        if getattr(self.department, "slug", "").lower() == MANAGER_SLUG and self.role_type != self.TEACHER:
            raise ValidationError("قسم المدير يقبل تكليف موظفين فقط (لا يوجد مسؤول قسم).")

    def save(self, *args, **kwargs):
        # إجبار الدور داخل القسم على TEACHER لقسم المدير
        if getattr(self.department, "slug", "").lower() == MANAGER_SLUG:
            self.role_type = self.TEACHER
        super().save(*args, **kwargs)


# =========================
# عضوية المدرسة (Teacher ↔ School)
# =========================
class SchoolMembership(models.Model):
    class RoleType(models.TextChoices):
        TEACHER = "teacher", "معلم"
        MANAGER = "manager", "مدير مدرسة"
        REPORT_VIEWER = "report_viewer", "مشرف تقارير (عرض فقط)"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="memberships",
        verbose_name="المدرسة",
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="school_memberships",
        verbose_name="المستخدم",
    )
    role_type = models.CharField(
        "الدور داخل المدرسة",
        max_length=16,
        choices=RoleType.choices,
        default=RoleType.TEACHER,
    )
    is_active = models.BooleanField("نشط؟", default=True)
    created_at = models.DateTimeField("أُنشئ في", auto_now_add=True)

    class Meta:
        unique_together = [("school", "teacher", "role_type")]
        constraints = [
            # مدرسة واحدة لا يمكن أن يكون لها أكثر من مدير نشط واحد
            models.UniqueConstraint(
                fields=["school"],
                # نستخدم القيمة النصية "manager" لتفادي NameError أثناء تعريف الكلاس
                condition=models.Q(role_type="manager", is_active=True),
                name="uniq_active_manager_per_school",
            )
        ]
        indexes = [
            models.Index(fields=["school"]),
            models.Index(fields=["teacher"]),
        ]
        verbose_name = "عضوية مدرسة"
        verbose_name_plural = "عضويات المدارس"

    def __str__(self) -> str:
        return f"{self.teacher} @ {self.school} ({self.role_type})"

    def save(self, *args, **kwargs):
        """فرض حد المعلمين حسب باقة المدرسة.

        المتطلبات:
        - لا يُحسب مدير المدرسة ضمن الحد (role_type=MANAGER).
        - الحد يُحسب على عدد حسابات المعلمين المرتبطين بالمدرسة (عضويات SchoolMembership بدور TEACHER)
          بغض النظر عن is_active.
        - الحذف يفتح مقعدًا (بما أنه يزيل العضوية).

        ملاحظة مهمة:
        - نطبق المنع فقط عند إنشاء عضوية TEACHER جديدة، أو عند تحويل/نقل عضوية إلى TEACHER/مدرسة أخرى.
          لا نمنع تحديثات بسيطة لعضوية موجودة (مثل تغيير is_active) حتى لو كانت المدرسة متجاوزة للحد تاريخيًا.
        """
        from django.core.exceptions import ValidationError

        should_enforce = self.pk is None
        if not should_enforce and self.pk is not None:
            try:
                prev = (
                    SchoolMembership.objects.filter(pk=self.pk)
                    .only("role_type", "school_id", "is_active")
                    .first()
                )
                if prev is not None and (
                    prev.role_type != self.role_type
                    or prev.school_id != self.school_id
                    or (not bool(prev.is_active) and bool(self.is_active))
                ):
                    should_enforce = True
            except Exception:
                # إن تعذرت المقارنة، لا نطبق المنع على تحديث عضوية موجودة
                should_enforce = False

        if should_enforce and self.role_type == self.RoleType.TEACHER:
            subscription = getattr(self.school, "subscription", None)
            if subscription is None or bool(getattr(subscription, "is_expired", True)):
                raise ValidationError("لا يوجد اشتراك فعّال لهذه المدرسة.")

            plan = getattr(subscription, "plan", None)
            max_teachers = int(getattr(plan, "max_teachers", 0) or 0)
            if max_teachers > 0:
                current_count = (
                    SchoolMembership.objects.filter(
                        school=self.school,
                        role_type=self.RoleType.TEACHER,
                    )
                    .exclude(pk=self.pk)
                    .count()
                )
                if current_count >= max_teachers:
                    raise ValidationError(f"لا يمكن إضافة أكثر من {max_teachers} معلّم لهذه المدرسة حسب الباقة.")

        # ✅ حد أقصى لمشرفي التقارير (عرض فقط): 2 نشطين لكل مدرسة
        if should_enforce and self.role_type == self.RoleType.REPORT_VIEWER and bool(self.is_active):
            active_viewers = (
                SchoolMembership.objects.filter(
                    school=self.school,
                    role_type=self.RoleType.REPORT_VIEWER,
                    is_active=True,
                )
                .exclude(pk=self.pk)
                .count()
            )
            if active_viewers >= 2:
                raise ValidationError("لا يمكن إضافة أكثر من 2 مشرف تقارير (عرض فقط) لهذه المدرسة.")

        return super().save(*args, **kwargs)


# =========================
# مرجع أنواع التقارير الديناميكي
# =========================
class ReportType(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="report_types",
        verbose_name="المدرسة",
        help_text="يظهر هذا النوع فقط في المدرسة المحددة.",
    )
    code = models.SlugField("الكود", max_length=40)
    name = models.CharField("الاسم", max_length=120)
    description = models.TextField("الوصف", blank=True)
    order = models.PositiveIntegerField("الترتيب", default=0)
    is_active = models.BooleanField("نشط", default=True)
    created_at = models.DateTimeField("أُنشئ", auto_now_add=True)
    updated_at = models.DateTimeField("تحديث", auto_now=True)

    class Meta:
        ordering = ("order", "name")
        constraints = [
            models.UniqueConstraint(
                fields=["school", "code"],
                condition=models.Q(school__isnull=False),
                name="uniq_reporttype_code_per_school",
            ),
            models.UniqueConstraint(
                fields=["code"],
                condition=models.Q(school__isnull=True),
                name="uniq_global_reporttype_code",
            ),
        ]
        indexes = [
            models.Index(fields=["school", "code"]),
        ]
        verbose_name = "نوع تقرير"
        verbose_name_plural = "أنواع التقارير"

    def __str__(self) -> str:
        return self.name or self.code

    def save(self, *args, **kwargs):
        # تطبيع code إلى lowercase
        if self.code:
            self.code = self.code.strip().lower()
        super().save(*args, **kwargs)


# =========================
# نموذج التقرير العام
# =========================
class Report(models.Model):
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="reports",
        verbose_name="المدرسة",
        db_index=True,
    )

    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="reports",
        db_index=True,
        verbose_name="المعلم (حساب)",
    )

    # اسم المعلم وقت الإنشاء (للتجميد)
    teacher_name = models.CharField(
        max_length=120,
        blank=True,
        verbose_name="اسم المعلم (وقت الإنشاء)",
        help_text="يُحفظ هنا الاسم الظاهر بغض النظر عن تغيّر اسم الحساب لاحقًا.",
    )

    title = models.CharField("العنوان / البرنامج", max_length=255, db_index=True)
    report_date = models.DateField("تاريخ التقرير / البرنامج", db_index=True)
    day_name = models.CharField("اليوم", max_length=20, blank=True, null=True)

    beneficiaries_count = models.PositiveIntegerField(
        "عدد المستفيدين",
        blank=True,
        null=True,
        validators=[MinValueValidator(0)],
        help_text="اتركه فارغًا إذا لا ينطبق",
    )

    idea = models.TextField("الوصف / فكرة التقرير", blank=True, null=True)

    # التصنيف ديناميكي عبر FK
    category = models.ForeignKey(
        "ReportType",
        on_delete=models.PROTECT,     # منع حذف النوع إن كان مستخدمًا
        null=True, blank=True,        # مؤقتًا لتسهيل الهجرة؛ يمكن جعلها إلزامية لاحقًا
        verbose_name="التصنيف",
        related_name="reports",
        db_index=True,
    )

    image1 = models.ImageField(upload_to=_report_image_upload_to, blank=True, null=True, validators=[validate_image_file])
    image2 = models.ImageField(upload_to=_report_image_upload_to, blank=True, null=True, validators=[validate_image_file])
    image3 = models.ImageField(upload_to=_report_image_upload_to, blank=True, null=True, validators=[validate_image_file])
    image4 = models.ImageField(upload_to=_report_image_upload_to, blank=True, null=True, validators=[validate_image_file])


    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["created_at"]),
            models.Index(fields=["teacher", "category"]),
            models.Index(fields=["report_date"]),
            # ✅ في الإنتاج غالبًا نستعلم دائمًا داخل مدرسة محددة
            models.Index(fields=["school", "report_date"]),
            models.Index(fields=["school", "created_at"]),
            models.Index(fields=["school", "category"]),
        ]
        verbose_name = "تقرير"
        verbose_name_plural = "التقارير"

    def __str__(self):
        display_name = self.teacher_name.strip() if self.teacher_name else getattr(self.teacher, "name", "")
        cat = getattr(self.category, "name", "بدون تصنيف")
        return f"{self.title} - {cat} - {display_name} ({self.report_date})"

    @property
    def teacher_display_name(self) -> str:
        return (self.teacher_name or getattr(self.teacher, "name", "") or "").strip()

    def save(self, *args, **kwargs):
        # اليوم باللغة العربية
        if self.report_date and not self.day_name:
            days = {
                1: "الاثنين", 2: "الثلاثاء", 3: "الأربعاء", 4: "الخميس",
                5: "الجمعة", 6: "السبت", 7: "الأحد"
            }
            try:
                self.day_name = days.get(self.report_date.isoweekday())
            except Exception:
                pass

        # تجميد اسم المعلّم وقت الإنشاء إن لم يُملأ
        if not self.teacher_name and getattr(self, "teacher_id", None):
            try:
                self.teacher_name = getattr(self.teacher, "name", "") or ""
            except Exception:
                pass

        super().save(*args, **kwargs)


# =========================
# ملف إنجاز المعلّم (سنوي)
# =========================
class TeacherAchievementFile(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "مسودة"
        SUBMITTED = "submitted", "بانتظار الاعتماد"
        RETURNED = "returned", "مُعاد للمعلّم"
        APPROVED = "approved", "معتمد"

    teacher = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="achievement_files",
        verbose_name="المعلّم",
        db_index=True,
    )
    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="achievement_files",
        verbose_name="المدرسة",
        db_index=True,
    )

    academic_year = models.CharField(
        "السنة الدراسية (هجري)",
        max_length=9,
        help_text="مثال: 1447-1448",
        db_index=True,
    )
    status = models.CharField(
        "الحالة",
        max_length=12,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )

    submitted_at = models.DateTimeField("تاريخ الإرسال", null=True, blank=True)
    decided_at = models.DateTimeField("تاريخ القرار", null=True, blank=True)
    decided_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="achievement_files_decided",
        verbose_name="اعتماد بواسطة",
    )

    # Snapshot بيانات عامة (تظهر في PDF)
    teacher_name = models.CharField("اسم المعلّم", max_length=150, blank=True, default="")
    teacher_phone = models.CharField("رقم الجوال", max_length=20, blank=True, default="")
    school_name = models.CharField("اسم المدرسة", max_length=200, blank=True, default="")
    school_stage = models.CharField("المرحلة", max_length=32, blank=True, default="")

    # بيانات عامة تُعبّأ سنويًا (مع زر استيراد)
    qualifications = models.TextField("المؤهلات", blank=True, default="")
    professional_experience = models.TextField("الخبرات المهنية", blank=True, default="")
    specialization = models.TextField("التخصص", blank=True, default="")
    teaching_load = models.TextField("نصاب الحصص", blank=True, default="")
    subjects_taught = models.TextField("مواد التدريس", blank=True, default="")
    contact_info = models.TextField("بيانات التواصل", blank=True, default="")

    manager_notes = models.TextField("ملاحظات مدير المدرسة", blank=True, default="")

    pdf_file = models.FileField(
        "ملف PDF",
        upload_to=_achievement_pdf_upload_to,
        storage=PublicRawMediaStorage(),
        blank=True,
        null=True,
        validators=[validate_pdf_file],
    )
    pdf_generated_at = models.DateTimeField("آخر توليد PDF", null=True, blank=True)

    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)

    class Meta:
        verbose_name = "ملف إنجاز"
        verbose_name_plural = "ملفات الإنجاز"
        constraints = [
            models.UniqueConstraint(
                fields=["teacher", "school", "academic_year"],
                name="uniq_teacher_achievement_per_year",
            )
        ]
        indexes = [
            models.Index(fields=["school", "academic_year", "status"]),
            models.Index(fields=["teacher", "academic_year"]),
        ]

    def clean(self):
        self.academic_year = _normalize_academic_year_hijri(self.academic_year)
        _validate_academic_year_hijri(self.academic_year)
        return super().clean()

    def save(self, *args, **kwargs):
        # snapshot تلقائي (لا يعتمد على إدخال المستخدم)
        try:
            self.academic_year = _normalize_academic_year_hijri(self.academic_year)
        except Exception:
            pass
        try:
            if self.teacher_id:
                self.teacher_name = self.teacher_name or getattr(self.teacher, "name", "") or ""
                self.teacher_phone = self.teacher_phone or getattr(self.teacher, "phone", "") or ""
        except Exception:
            pass
        try:
            if self.school_id:
                self.school_name = self.school_name or getattr(self.school, "name", "") or ""
                self.school_stage = self.school_stage or getattr(self.school, "stage", "") or ""
        except Exception:
            pass
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.teacher_name or self.teacher_id} - {self.academic_year}"


class AchievementSection(models.Model):
    class Code(models.IntegerChoices):
        SECTION_1 = 1, "1- أداء الواجبات الوظيفية"
        SECTION_2 = 2, "2- التفاعل مع المجتمع المهني"
        SECTION_3 = 3, "3- التفاعل مع أولياء الأمور"
        SECTION_4 = 4, "4- التنوع في استراتيجيات التدريس"
        SECTION_5 = 5, "5- تحسين نتائج المتعلمين"
        SECTION_6 = 6, "6- إعداد وتنفيذ خطة التعلم"
        SECTION_7 = 7, "7- توظيف تقنيات ووسائل التعلم المناسبة"
        SECTION_8 = 8, "8- تهيئة بيئة تعليمية"
        SECTION_9 = 9, "9- الإدارة الصفية"
        SECTION_10 = 10, "10- تحليل نتائج المتعلمين وتشخيص مستوياتهم"
        SECTION_11 = 11, "11- تنوع أساليب التقويم"

    file = models.ForeignKey(
        TeacherAchievementFile,
        on_delete=models.CASCADE,
        related_name="sections",
        verbose_name="ملف الإنجاز",
        db_index=True,
    )
    code = models.PositiveSmallIntegerField("المحور", choices=Code.choices)
    title = models.CharField("العنوان", max_length=200, blank=True, default="")
    teacher_notes = models.TextField("ملاحظات المعلّم", blank=True, default="")

    class Meta:
        verbose_name = "محور ملف إنجاز"
        verbose_name_plural = "محاور ملفات الإنجاز"
        constraints = [
            models.UniqueConstraint(fields=["file", "code"], name="uniq_achievement_section_per_file")
        ]
        ordering = ["code", "id"]

    def save(self, *args, **kwargs):
        if not self.title:
            try:
                self.title = dict(self.Code.choices).get(int(self.code), "")
            except Exception:
                pass
        return super().save(*args, **kwargs)

    def __str__(self) -> str:
        return f"{self.file_id} - {self.code}"


class AchievementEvidenceImage(models.Model):
    section = models.ForeignKey(
        AchievementSection,
        on_delete=models.CASCADE,
        related_name="evidence_images",
        verbose_name="المحور",
        db_index=True,
    )
    image = models.ImageField(
        "صورة الشاهد",
        upload_to=_achievement_evidence_upload_to,
        validators=[validate_image_file],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "صورة شاهد"
        verbose_name_plural = "صور الشواهد"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"EvidenceImage #{self.pk} (section {self.section_id})"


# =========================
# إعدادات المنصة (Singleton)
# =========================
class PlatformSettings(models.Model):
    """إعدادات عامة للمنصة تُدار من مدير النظام.

    نستخدم سجلًا واحدًا فقط (singleton) ونتعامل معه عبر get_solo().
    """

    share_link_default_days = models.PositiveSmallIntegerField(
        "مدة صلاحية رابط المشاركة (بالأيام)",
        default=7,
        help_text="المدة الافتراضية لروابط مشاركة التقارير/ملفات الإنجاز.",
    )

    updated_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_settings_updates",
        verbose_name="آخر تعديل بواسطة",
    )
    updated_at = models.DateTimeField("آخر تعديل", auto_now=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)

    class Meta:
        verbose_name = "إعدادات المنصة"
        verbose_name_plural = "إعدادات المنصة"

    @classmethod
    def get_solo(cls) -> "PlatformSettings":
        obj = cls.objects.order_by("id").first()
        if obj is not None:
            return obj
        return cls.objects.create()

    def __str__(self) -> str:
        return "إعدادات المنصة"


def get_share_link_default_days(school: Optional["School"] = None) -> int:
    """يرجع مدة صلاحية روابط المشاركة بالأيام.

    الأولوية:
    1. القيمة المحددة في نموذج School (إن تم تمريرها)
    2. settings.SHARE_LINK_DEFAULT_DAYS
    3. القيمة الافتراضية 7 أيام
    
    Args:
        school: نموذج المدرسة (اختياري)
    
    Returns:
        عدد الأيام (الحد الأدنى 1)
    """
    days = 7  # القيمة الافتراضية
    
    # محاولة قراءة القيمة من المدرسة
    if school is not None:
        try:
            school_days = getattr(school, "share_link_default_days", None)
            if school_days is not None:
                days = int(school_days)
        except Exception:
            pass
    
    # إذا لم يتم تمرير مدرسة أو لم تكن لديها قيمة، نقرأ من settings
    if days == 7:  # لم يتم تعديلها من المدرسة
        try:
            days = int(getattr(settings, "SHARE_LINK_DEFAULT_DAYS", 7))
        except Exception:
            days = 7
    
    # التأكد من أن القيمة موجبة
    if days <= 0:
        days = 7
    
    return days


# =========================
# روابط مشاركة عامة (بدون حساب)
# =========================
class ShareLink(models.Model):
    class Kind(models.TextChoices):
        REPORT = "report", "تقرير"
        ACHIEVEMENT = "achievement", "ملف إنجاز"

    token = models.CharField("Token", max_length=64, unique=True, db_index=True)
    kind = models.CharField("النوع", max_length=20, choices=Kind.choices, db_index=True)

    created_by = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="share_links",
        verbose_name="تم الإنشاء بواسطة",
    )
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="share_links",
        verbose_name="المدرسة",
        db_index=True,
    )

    report = models.ForeignKey(
        "Report",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="share_links",
        verbose_name="التقرير",
        db_index=True,
    )
    achievement_file = models.ForeignKey(
        "TeacherAchievementFile",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="share_links",
        verbose_name="ملف الإنجاز",
        db_index=True,
    )

    is_active = models.BooleanField("مفعّل", default=True, db_index=True)
    expires_at = models.DateTimeField("ينتهي في", db_index=True)
    last_accessed_at = models.DateTimeField("آخر وصول", null=True, blank=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True)

    class Meta:
        verbose_name = "رابط مشاركة"
        verbose_name_plural = "روابط مشاركة"
        indexes = [
            models.Index(fields=["kind", "is_active", "expires_at"]),
            models.Index(fields=["report", "is_active", "expires_at"]),
            models.Index(fields=["achievement_file", "is_active", "expires_at"]),
        ]
        constraints = [
            models.CheckConstraint(
                name="sharelink_kind_target_consistent",
                check=(
                    models.Q(kind="report", report__isnull=False, achievement_file__isnull=True)
                    | models.Q(kind="achievement", report__isnull=True, achievement_file__isnull=False)
                ),
            )
        ]

    @staticmethod
    def default_expires_at() -> timezone.datetime:
        return timezone.now() + timedelta(days=get_share_link_default_days())

    @staticmethod
    def generate_token() -> str:
        # طول ~43 حرف عند 32 bytes (مع هامش)
        return secrets.token_urlsafe(32)

    @property
    def is_expired(self) -> bool:
        try:
            return timezone.now() >= self.expires_at
        except Exception:
            return True

    def __str__(self) -> str:
        target = self.report_id or self.achievement_file_id
        return f"{self.get_kind_display()} ({target})"


# =========================
# منظومة التذاكر الموحّدة
# =========================
MAX_ATTACHMENT_MB = 5
_MAX_BYTES = MAX_ATTACHMENT_MB * 1024 * 1024


def validate_attachment_size(file_obj):
    """تحقق الحجم ≤ 5MB"""
    if getattr(file_obj, "size", 0) > _MAX_BYTES:
        raise ValidationError(f"حجم المرفق يتجاوز {MAX_ATTACHMENT_MB}MB.")


class Ticket(models.Model):
    class Status(models.TextChoices):
        OPEN = "open", "جديد"
        IN_PROGRESS = "in_progress", "قيد المعالجة"
        DONE = "done", "مكتمل"
        REJECTED = "rejected", "مرفوض"

    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tickets",
        verbose_name="المدرسة",
        db_index=True,
    )

    creator = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="tickets_created",
        verbose_name="المرسل",
        db_index=True,
    )

    # القسم ديناميكي كـ FK
    department = models.ForeignKey(
        Department,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="tickets",
        verbose_name="القسم",
        db_index=True,
    )

    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        related_name="tickets_assigned",
        verbose_name="المستلم",
        blank=True,
        null=True,
        db_index=True,
    )

    # ✅ مستلمون متعددون (مع بقاء assignee كمرجع/مسؤول رئيسي للتوافق الخلفي)
    recipients = models.ManyToManyField(
        settings.AUTH_USER_MODEL,
        through="TicketRecipient",
        related_name="tickets_received",
        blank=True,
        verbose_name="المستلمون",
    )

    title = models.CharField("عنوان الطلب", max_length=255)
    body = models.TextField("تفاصيل الطلب", blank=True, null=True)

    # ✅ مرفق يُرفع إلى التخزين الافتراضي (R2 أو محلي)
    attachment = models.FileField(
        "مرفق",
        upload_to=_ticket_attachment_upload_to,
        storage=PublicRawMediaStorage(),   # عام + raw
        blank=True,
        null=True,
        validators=[
            FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "doc", "docx"]),
            validate_attachment_file,
        ],
        help_text=f"يسمح بـ PDF/صور/DOCX حتى {MAX_ATTACHMENT_MB}MB",
    )

    status = models.CharField(
        "الحالة",
        max_length=20,
        choices=Status.choices,
        default=Status.OPEN,
        db_index=True,
    )
    
    is_platform = models.BooleanField(
        "دعم فني للمنصة؟", 
        default=False,
        help_text="إذا تم تحديده، يعتبر هذا الطلب موجهاً لإدارة المنصة وليس للمدرسة داخلياً."
    )

    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "طلب"
        verbose_name_plural = "الطلبات"
        indexes = [
            models.Index(fields=["department", "status", "created_at"]),
            models.Index(fields=["assignee", "status"]),
            # ✅ فهارس شائعة لصفحات المدرسة/الاستعلامات
            models.Index(fields=["school", "status", "created_at"]),
            models.Index(fields=["school", "assignee", "status"]),
        ]

    def __str__(self):
        return f"Ticket #{self.pk} - {self.title[:40]}"


class TicketRecipient(models.Model):
    """ربط التذكرة بمستلمين متعددين.

    لا نخزن حالة لكل مستلم لأن الخيار (1) يعتمد حالة مشتركة للتذكرة.
    """

    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="ticket_recipients",
        db_index=True,
    )
    teacher = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ticket_recipient_links",
        db_index=True,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "مستلم تذكرة"
        verbose_name_plural = "مستلمو التذاكر"
        constraints = [
            models.UniqueConstraint(fields=["ticket", "teacher"], name="uniq_ticket_recipient"),
        ]

    def __str__(self) -> str:
        return f"Ticket #{self.ticket_id} → {getattr(self.teacher, 'name', self.teacher_id)}"

    # ======== خصائص مساعدة للقوالب ========
    @property
    def attachment_name_lower(self) -> str:
        return (getattr(self.attachment, "name", "") or "").lower()

    @property
    def attachment_is_image(self) -> bool:
        return self.attachment_name_lower.endswith((".jpg", ".jpeg", ".png", ".webp"))

    @property
    def attachment_is_pdf(self) -> bool:
        return self.attachment_name_lower.endswith(".pdf")

    @property
    def attachment_download_url(self) -> str:
        """
        • أضف Content-Disposition عبر query كحل احتياطي لتلميح التحميل.
        """
        url = getattr(self.attachment, "url", "") or ""
        if not url:
            return ""

        filename = os.path.basename(getattr(self.attachment, "name", "")) or "download"

        # تلميح للتحميل
        sep = "&" if "?" in url else "?"
        dispo = quote(f"attachment; filename*=UTF-8''{filename}", safe="")
        return f"{url}{sep}response-content-disposition={dispo}"


class TicketNote(models.Model):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="notes",
        verbose_name="التذكرة"
    )
    author = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="ticket_notes",
        verbose_name="كاتب الملاحظة"
    )
    body = models.TextField("الملاحظة")
    is_public = models.BooleanField("ظاهرة للمرسل؟", default=True)
    created_at = models.DateTimeField("تاريخ الإضافة", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "ملاحظة طلب"
        verbose_name_plural = "ملاحظات الطلبات"

    def __str__(self):
        return f"Note #{self.pk} on Ticket #{self.ticket_id}"


# =========================
# نماذج تراثية (اختياري للأرشفة)
# =========================
REQUEST_DEPARTMENTS = [
    (MANAGER_SLUG, "المدير"),
    ("activity_officer", "مسؤول النشاط"),
    ("volunteer_officer", "مسؤول التطوع"),
    ("affairs_officer", "مسؤول الشؤون المدرسية"),
    ("admin_officer", "مسؤول الشؤون الإدارية"),
]


class RequestTicket(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "جديد"
        IN_PROGRESS = "in_progress", "قيد المعالجة"
        DONE = "done", "تم الإنجاز"
        REJECTED = "rejected", "مرفوض"

    requester = models.ForeignKey(
        Teacher,
        on_delete=models.CASCADE,
        related_name="created_tickets",
        verbose_name="صاحب الطلب",
        db_index=True,
    )
    department = models.CharField("القسم/الجهة", max_length=32, choices=REQUEST_DEPARTMENTS, db_index=True)
    assignee = models.ForeignKey(
        Teacher,
        on_delete=models.SET_NULL,
        related_name="assigned_tickets",
        verbose_name="المستلم",
        null=True,
        blank=True,
    )
    title = models.CharField("عنوان الطلب", max_length=200)
    body = models.TextField("تفاصيل الطلب")
    attachment = models.FileField(
        "مرفق (اختياري)",
        upload_to=_ticket_attachment_upload_to,
        blank=True,
        null=True,
        validators=[
            FileExtensionValidator(["pdf", "jpg", "jpeg", "png", "doc", "docx"]),
            validate_attachment_file,
        ],
    )
    status = models.CharField("الحالة", max_length=20, choices=Status.choices, default=Status.NEW, db_index=True)
    created_at = models.DateTimeField("تاريخ الإنشاء", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("تاريخ التحديث", auto_now=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["department", "status"]),
            models.Index(fields=["assignee", "status"]),
        ]
        verbose_name = "طلب (تراثي)"
        verbose_name_plural = "طلبات (تراثية)"

    def __str__(self):
        return f"#{self.pk} - {self.title} ({self.get_status_display()})"


class RequestLog(models.Model):
    ticket = models.ForeignKey(RequestTicket, on_delete=models.CASCADE, related_name="logs", verbose_name="الطلب")
    actor = models.ForeignKey(Teacher, on_delete=models.SET_NULL, null=True, blank=True, verbose_name="منفّذ العملية")
    old_status = models.CharField("الحالة القديمة", max_length=20, choices=RequestTicket.Status.choices, blank=True)
    new_status = models.CharField("الحالة الجديدة", max_length=20, choices=RequestTicket.Status.choices, blank=True)
    note = models.TextField("ملاحظة", blank=True)
    created_at = models.DateTimeField("وقت الإنشاء", auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "سجل طلب (تراثي)"
        verbose_name_plural = "سجل الطلبات (تراثي)"

    def __str__(self):
        return f"Log for #{self.ticket_id} at {self.created_at:%Y-%m-%d %H:%M}"


# =========================
# إشارات تضمن القسم/الدور الدائم للمدير
# =========================
@receiver(post_migrate)
def ensure_manager_department_and_role(sender, **kwargs):
    """
    يضمن وجود دور المدير بعد الهجرات، ويضمن وجود قسم (الإدارة) داخل كل مدرسة.

    لماذا؟
    - الأقسام أصبحت مخصصة لكل مدرسة، لذا نحتاج Department(slug='manager') لكل مدرسة.
    - دور Role(slug='manager') يبقى كمرجع عام (اختياري) لبعض الشاشات/المهام.
    """
    try:
        with transaction.atomic():
            role, created = Role.objects.get_or_create(
                slug=MANAGER_SLUG,
                defaults={
                    "name": MANAGER_ROLE_LABEL,
                    "is_staff_by_default": True,
                    "can_view_all_reports": True,
                    "is_active": True,
                },
            )
            if not created:
                r_upd = []
                if role.name != MANAGER_ROLE_LABEL:
                    role.name = MANAGER_ROLE_LABEL
                    r_upd.append("name")
                if not role.is_staff_by_default:
                    role.is_staff_by_default = True
                    r_upd.append("is_staff_by_default")
                if not role.can_view_all_reports:
                    role.can_view_all_reports = True
                    r_upd.append("can_view_all_reports")
                if not role.is_active:
                    role.is_active = True
                    r_upd.append("is_active")
                if r_upd:
                    role.save(update_fields=r_upd)

            # ✅ قسم الإدارة لكل مدرسة
            try:
                schools = School.objects.all().only("id")
                for s in schools:
                    dep, _ = Department.objects.get_or_create(
                        school=s,
                        slug=MANAGER_SLUG,
                        defaults={"name": MANAGER_NAME, "role_label": MANAGER_ROLE_LABEL, "is_active": True},
                    )
                    updates = []
                    if dep.name != MANAGER_NAME:
                        dep.name = MANAGER_NAME
                        updates.append("name")
                    if dep.role_label != MANAGER_ROLE_LABEL:
                        dep.role_label = MANAGER_ROLE_LABEL
                        updates.append("role_label")
                    if not dep.is_active:
                        dep.is_active = True
                        updates.append("is_active")
                    if updates:
                        dep.save(update_fields=updates)
            except Exception:
                # لا نوقف post_migrate بسبب مشاكل بيانات
                pass
    except Exception:
        # لا نرفع خطأ أثناء post_migrate للحفاظ على استقرار الهجرات
        pass


# =========================
# الإشعارات
# =========================
class Notification(models.Model):
    title = models.CharField(max_length=120, blank=True, default="")
    message = models.TextField()
    is_important = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)

    # =========================
    # التواقيع (للتعاميم الإلزامية)
    # =========================
    requires_signature = models.BooleanField(
        "يتطلب توقيع؟",
        default=False,
        help_text="عند التفعيل يصبح الإشعار تعميمًا ويتطلب إقرار + إدخال الجوال للتوقيع.",
    )
    signature_deadline_at = models.DateTimeField(
        "آخر موعد للتوقيع",
        null=True,
        blank=True,
        help_text="اختياري: يظهر للمعلمين في صفحة التوقيع ويستخدم للتقارير.",
    )
    signature_ack_text = models.TextField(
        "نص الإقرار",
        blank=True,
        default="أقرّ بأنني اطلعت على هذا التعميم وفهمت ما ورد فيه وأتعهد بالالتزام به.",
    )
    created_at = models.DateTimeField(default=timezone.now)
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
        verbose_name="المدرسة المستهدفة",
        help_text="إن تُركت فارغة يكون الإشعار عامًا أو على مستوى كل المدارس.",
    )
    created_by = models.ForeignKey(
        Teacher, null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications_created"
    )

    class Meta:
        db_table = "reports_notification"
        ordering = ("-created_at", "-id")

    def __str__(self):
        return self.title or (self.message[:30] + ("..." if len(self.message) > 30 else ""))


class NotificationRecipient(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="recipients")
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name="notifications")
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    # توقيع التعميم (على مستوى المستلم)
    is_signed = models.BooleanField(default=False)
    signed_at = models.DateTimeField(null=True, blank=True)
    signature_attempt_count = models.PositiveSmallIntegerField(default=0)
    signature_last_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "reports_notification_recipient"
        indexes = [
            models.Index(fields=["teacher", "is_read", "-created_at"]),
            models.Index(fields=["teacher", "is_signed", "-created_at"]),
        ]
        unique_together = (("notification", "teacher"),)

    def __str__(self):
        return f"{self.teacher} ← {self.notification}"


# reports/models.py  (بعد class Ticket)
class TicketImage(models.Model):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name="التذكرة",
        db_index=True,
    )
    image = models.ImageField(
        "الصورة",
        upload_to=_ticket_image_upload_to,
        blank=False,
        null=False,
        validators=[validate_image_file],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "صورة تذكرة"
        verbose_name_plural = "صور التذكرة"

    def __str__(self):
        return f"TicketImage #{self.pk} for Ticket #{self.ticket_id}"


# =========================
# إدارة الاشتراكات والمالية
# =========================

class SubscriptionPlan(models.Model):
    name = models.CharField("اسم الباقة", max_length=100)
    price = models.DecimalField("السعر", max_digits=10, decimal_places=2)
    days_duration = models.PositiveIntegerField(
        "المدة بالأيام", 
        help_text="مدة الباقة الافتراضية بالأيام (مثلاً 90 للفصل، 365 للسنة)"
    )
    description = models.TextField("المميزات", blank=True)
    is_active = models.BooleanField("نشطة", default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    max_teachers = models.PositiveIntegerField(
        "حد المعلمين",
        default=0,
        help_text="الحد الأقصى لعدد حسابات المعلمين داخل المدرسة. 0 = غير محدود.",
    )

    class Meta:
        verbose_name = "باقة اشتراك"
        verbose_name_plural = "باقات الاشتراكات"

    def __str__(self):
        return f"{self.name} ({self.price} ريال)"


class SchoolSubscription(models.Model):
    school = models.OneToOneField(
        School,
        on_delete=models.CASCADE,
        related_name="subscription",
        verbose_name="المدرسة"
    )
    plan = models.ForeignKey(
        SubscriptionPlan,
        on_delete=models.PROTECT,
        verbose_name="الباقة الحالية"
    )
    start_date = models.DateField("تاريخ البدء")
    end_date = models.DateField("تاريخ الانتهاء", db_index=True)
    is_active = models.BooleanField(
        "نشط يدوياً", 
        default=True, 
        help_text="يمكن استخدامه لتعطيل الاشتراك مؤقتاً بغض النظر عن التاريخ"
    )

    canceled_at = models.DateTimeField(
        "تاريخ الإلغاء",
        null=True,
        blank=True,
        help_text="يُعبّأ عند إلغاء الاشتراك من مدير النظام.",
    )
    cancel_reason = models.TextField(
        "سبب الإلغاء",
        blank=True,
        help_text="يظهر للمدرسة عند إلغاء الاشتراك.",
    )
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "اشتراك مدرسة"
        verbose_name_plural = "اشتراكات المدارس"
        indexes = [
            models.Index(fields=['end_date', 'is_active']),
        ]

    def __str__(self):
        return f"اشتراك {self.school.name} - ينتهي في {self.end_date}"

    def save(self, *args, **kwargs):
        """ضبط تواريخ الاشتراك تلقائياً.

        المطلوب:
        - عند إنشاء اشتراك جديد: start_date = اليوم (ميلادي) و end_date حسب plan.days_duration.
        - عند تغيير الباقة (plan) في أي مكان (بما فيه Django admin): نعتبره تجديداً ونُعيد حساب التواريخ من اليوم.

        ملاحظة: لا نُعيد حساب التواريخ عند أي تعديل آخر (مثل تغيير is_active فقط)
        حتى لا يتم تمديد/تجديد الاشتراك بالخطأ.
        """
        from datetime import timedelta

        today = timezone.localdate()

        should_recalc = self.pk is None
        if not should_recalc and self.pk is not None:
            try:
                prev = SchoolSubscription.objects.filter(pk=self.pk).only("plan_id").first()
                if prev is not None and prev.plan_id != self.plan_id:
                    should_recalc = True
            except Exception:
                # في حال تعذّر مقارنة التغيير، لا نغيّر التواريخ على اشتراك موجود
                should_recalc = False

        if should_recalc:
            self.start_date = today
            days = int(getattr(self.plan, "days_duration", 0) or 0)
            if days <= 0:
                self.end_date = today
            else:
                # end_date = اليوم + (المدة - 1) حتى تكون الأيام الفعلية = days_duration
                self.end_date = today + timedelta(days=days - 1)

        return super().save(*args, **kwargs)

    @property
    def is_expired(self):
        if bool(self.is_cancelled):
            return True
        if not self.is_active:
            return True
        return timezone.now().date() > self.end_date

    @property
    def is_cancelled(self) -> bool:
        # الإلغاء المقصود: وجود تاريخ إلغاء (أو سبب) مع إيقاف الاشتراك.
        # لا نعتمد فقط على is_active=False لأن ذلك قد يُستخدم للإيقاف المؤقت.
        if bool(self.canceled_at) and not bool(self.is_active):
            return True
        if (self.cancel_reason or "").strip() and not bool(self.is_active):
            return True
        return False

    @property
    def days_remaining(self):
        delta = self.end_date - timezone.now().date()
        return delta.days


class Payment(models.Model):
    class Status(models.TextChoices):
        PENDING = "pending", "قيد المراجعة"
        APPROVED = "approved", "مقبول"
        REJECTED = "rejected", "مرفوض"
        CANCELLED = "cancelled", "ملغي"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="payments",
        verbose_name="المدرسة"
    )

    requested_plan = models.ForeignKey(
        "SubscriptionPlan",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="payment_requests",
        verbose_name="الباقة المطلوبة",
    )

    subscription = models.ForeignKey(
        SchoolSubscription,
        on_delete=models.SET_NULL,
        null=True, blank=True,
        related_name="payments",
        verbose_name="الاشتراك المرتبط"
    )
    amount = models.DecimalField("المبلغ", max_digits=10, decimal_places=2)
    receipt_image = models.ImageField(
        "صورة الإيصال",
        upload_to=_payment_receipt_upload_to,
        help_text="يرجى إرفاق صورة التحويل البنكي",
        validators=[validate_image_file],
    )
    payment_date = models.DateField("تاريخ التحويل", default=timezone.now)
    status = models.CharField(
        "الحالة", 
        max_length=20, 
        choices=Status.choices, 
        default=Status.PENDING, 
        db_index=True
    )
    notes = models.TextField("ملاحظات الإدارة", blank=True)
    
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        verbose_name="قام بالرفع"
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "عملية دفع"
        verbose_name_plural = "المدفوعات والإيرادات"
        ordering = ['-created_at']

    def __str__(self):
        return f"دفع #{self.id} - {self.school.name} - {self.amount}"


# =========================
# إشارات معالجة الصور (Celery)
# =========================
@receiver(post_save, sender=Report)
def trigger_report_background_tasks(sender, instance, created, **kwargs):
    """
    عند إنشاء تقرير جديد أو تحديثه، نقوم بجدولة المهام في الخلفية وتحديث الكاش.
    """
    from django.core.cache import cache
    if instance.school_id:
        cache.delete(f"admin_stats_{instance.school_id}")
    cache.delete("platform_admin_stats")

    from .tasks import process_report_images
    from .utils import run_task_safe

    # 1. معالجة الصور (إذا وجدت)
    has_images = any([instance.image1, instance.image2, instance.image3, instance.image4])
    
    if has_images:
        # معالجة الصور فقط (لا نقوم بتوليد PDF)
        run_task_safe(process_report_images, instance.pk)
    # إذا لم توجد صور: لا يوجد أي مهام مطلوبة هنا


@receiver(post_save, sender=Ticket)
def trigger_ticket_notifications(sender, instance, created, **kwargs):
    """
    عند إنشاء تذكرة جديدة، نقوم بإرسال إشعارات للمسؤولين المعنيين وتحديث الكاش.
    """
    from django.core.cache import cache
    if instance.school_id:
        cache.delete(f"admin_stats_{instance.school_id}")
    cache.delete("platform_admin_stats")

    if not created:
        return

    from .utils import create_system_notification

    title = f"تذكرة جديدة: {instance.title}"
    message = f"تم إنشاء طلب جديد بواسطة {instance.creator.name}. الحالة: {instance.get_status_display()}"

    if instance.is_platform:
        # تذكرة منصة: إشعار للسوبر يوزر
        superusers = Teacher.objects.filter(is_superuser=True).values_list('id', flat=True)
        if superusers:
            create_system_notification(
                title=f"🆘 دعم فني: {instance.title}",
                message=message,
                teacher_ids=list(superusers),
                is_important=True
            )
    else:
        # تذكرة مدرسة: إشعار للمدير ومسؤول القسم
        recipients = set()
        
        # 1. مدير المدرسة
        if instance.school:
            managers = SchoolMembership.objects.filter(
                school=instance.school,
                role_type=SchoolMembership.RoleType.MANAGER,
                is_active=True
            ).values_list('teacher_id', flat=True)
            recipients.update(managers)

        # 2. مسؤول القسم (إذا تم تحديد قسم)
        if instance.department:
            officers = DepartmentMembership.objects.filter(
                department=instance.department,
                role_type=DepartmentMembership.OFFICER
            ).values_list('teacher_id', flat=True)
            recipients.update(officers)

        if recipients:
            create_system_notification(
                title=title,
                message=message,
                school=instance.school,
                teacher_ids=list(recipients)
            )


@receiver(post_save, sender=TicketImage)
def trigger_ticket_image_processing(sender, instance, created, **kwargs):
    """
    عند رفع صورة تذكرة، نقوم بجدولة معالجتها في الخلفية.
    """
    from .tasks import process_ticket_image
    if instance.image:
        try:
            transaction.on_commit(lambda: process_ticket_image.delay(instance.pk))
        except Exception:
            pass


# =========================
# سجل العمليات (Audit Logs)
# =========================
class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "إنشاء"
        UPDATE = "update", "تعديل"
        DELETE = "delete", "حذف"
        LOGIN = "login", "تسجيل دخول"
        LOGOUT = "logout", "تسجيل خروج"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="audit_logs",
        verbose_name="المدرسة",
        null=True,
        blank=True
    )
    teacher = models.ForeignKey(
        "Teacher",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name="المستخدم"
    )
    action = models.CharField("العملية", max_length=20, choices=Action.choices)
    model_name = models.CharField("اسم النموذج", max_length=100, blank=True)
    object_id = models.PositiveIntegerField("معرف السجل", null=True, blank=True)
    object_repr = models.CharField("وصف السجل", max_length=255, blank=True)
    changes = models.JSONField("التغييرات", null=True, blank=True)
    ip_address = models.GenericIPAddressField("عنوان IP", null=True, blank=True)
    user_agent = models.TextField("متصفح المستخدم", blank=True)
    timestamp = models.DateTimeField("الوقت", auto_now_add=True)

    class Meta:
        ordering = ("-timestamp",)
        verbose_name = "سجل عمليات"
        verbose_name_plural = "سجلات العمليات"
        indexes = [
            models.Index(fields=["school", "timestamp"]),
            models.Index(fields=["teacher", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.teacher} - {self.get_action_display()} - {self.model_name} ({self.timestamp})"


# =========================
# Audit Log Signals
# =========================
@receiver(post_save)
def audit_log_save(sender, instance, created, **kwargs):
    from .middleware import is_audit_logging_suppressed

    if is_audit_logging_suppressed():
        return

    # قائمة النماذج التي نريد مراقبتها
    monitored_models = ["Report", "Teacher", "School", "Department", "Ticket", "SchoolSubscription"]
    if sender.__name__ not in monitored_models:
        return

    from .middleware import get_current_request
    request = get_current_request()
    if not request or not request.user.is_authenticated:
        return

    action = AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE
    
    # محاولة تحديد المدرسة
    school = getattr(instance, "school", None)
    if not school and sender.__name__ == "School":
        school = instance

    # تسجيل التغييرات (بشكل مبسط)
    changes = {}
    if not created:
        # في حالة التعديل، يمكننا لاحقاً إضافة منطق لمقارنة القيم القديمة والجديدة
        pass

    AuditLog.objects.create(
        school=school,
        teacher=request.user,
        action=action,
        model_name=sender.__name__,
        object_id=instance.pk if hasattr(instance, "pk") else None,
        object_repr=str(instance)[:255],
        changes=changes,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500]
    )


@receiver(models.signals.post_delete)
def audit_log_delete(sender, instance, **kwargs):
    from .middleware import is_audit_logging_suppressed

    if is_audit_logging_suppressed():
        return

    monitored_models = ["Report", "Teacher", "School", "Department", "Ticket"]
    if sender.__name__ not in monitored_models:
        return

    from .middleware import get_current_request
    request = get_current_request()
    if not request or not request.user.is_authenticated:
        return

    school = getattr(instance, "school", None)
    
    AuditLog.objects.create(
        school=school,
        teacher=request.user,
        action=AuditLog.Action.DELETE,
        model_name=sender.__name__,
        object_id=instance.pk if hasattr(instance, "pk") else None,
        object_repr=str(instance)[:255],
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500]
    )

