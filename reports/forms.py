# reports/forms.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional, List, Tuple
from io import BytesIO
import os
import logging

from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.contrib.auth.password_validation import (
    CommonPasswordValidator,
    MinimumLengthValidator,
    NumericPasswordValidator,
    UserAttributeSimilarityValidator,
    get_default_password_validators,
)
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import models, transaction
from django.db.models import Q
from django.utils.text import slugify
from django.utils import timezone

from .validators import validate_circular_attachment_file

# ==============================
# استيراد الموديلات (من models.py فقط)
# ==============================
from .models import (
    Teacher,
    Department,
    DepartmentMembership,
    ReportType,
    Report,
    Ticket,
    TicketNote,
    Notification,
    NotificationRecipient,
    School,
    SchoolMembership,
    SubscriptionPlan,
    SchoolSubscription,
    TeacherAchievementFile,
    AchievementSection,
    AchievementEvidenceImage,
)
from .services_legacy_roles import (
    sync_legacy_role_for_department,
    sync_legacy_teacher_role,
)

logger = logging.getLogger(__name__)

# Avoid repeating the same warning on every request when broker is not configured.
_NOTIF_CELERY_FALLBACK_WARNED = False

# (تراثي – اختياري)
try:
    from .models import RequestTicket, REQUEST_DEPARTMENTS  # type: ignore
    HAS_REQUEST_TICKET = True
except Exception:
    RequestTicket = None  # type: ignore
    REQUEST_DEPARTMENTS = []  # type: ignore
    HAS_REQUEST_TICKET = False

# ==============================
# أدوات تحقق عامة (SA-specific)
# ==============================
digits10 = RegexValidator(r"^\d{10}$", "يجب أن يتكون من 10 أرقام.")
sa_phone = RegexValidator(r"^0\d{9}$", "رقم الجوال يجب أن يبدأ بـ 0 ويتكون من 10 أرقام.")


def _school_job_title_choices(active_school: Optional["School"] = None) -> tuple[tuple[str, str], ...]:
    """Display job-title labels using the active school's gender, without changing stored values."""
    gender = (getattr(active_school, "gender", "") or "").strip().lower()
    girls_value = str(getattr(getattr(School, "Gender", None), "GIRLS", "girls")).strip().lower()
    is_girls = gender == girls_value
    return (
        (SchoolMembership.JobTitle.TEACHER, "معلمة" if is_girls else "معلم"),
        (SchoolMembership.JobTitle.ADMIN_STAFF, "موظفة إدارية" if is_girls else "موظف إداري"),
        (SchoolMembership.JobTitle.LAB_TECH, "محضرة مختبر" if is_girls else "محضر مختبر"),
    )


class MyProfilePhoneForm(forms.ModelForm):
    """تحديث رقم جوال المستخدم الحالي.

    مهم: phone هو USERNAME_FIELD، لذلك نتحقق من التفرد قبل الحفظ.
    """

    phone = forms.CharField(
        label="رقم الجوال",
        min_length=10,
        max_length=10,
        validators=[sa_phone],
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "05XXXXXXXX",
                "maxlength": "10",
                "inputmode": "numeric",
                "pattern": r"0\d{9}",
                "autocomplete": "tel",
            }
        ),
    )

    class Meta:
        model = Teacher
        fields = ["phone"]

    def clean_phone(self):
        phone = (self.cleaned_data.get("phone") or "").strip()
        if not phone:
            raise ValidationError("رقم الجوال مطلوب.")

        qs = Teacher.objects.filter(phone=phone)
        if getattr(self.instance, "pk", None):
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise ValidationError("هذا الرقم مستخدم بالفعل.")

        return phone


class MyPasswordChangeForm(PasswordChangeForm):
    """نموذج تغيير كلمة المرور مع تحسين شكل الحقول."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.password_min_length = 8
        self.password_requirements = self._build_password_requirements()
        for name, f in self.fields.items():
            try:
                f.widget.attrs.setdefault("class", "form-control")
                if name == "old_password":
                    f.widget.attrs.setdefault("autocomplete", "current-password")
                else:
                    f.widget.attrs.setdefault("autocomplete", "new-password")
            except Exception:
                pass

        self.fields["old_password"].widget.attrs.setdefault("placeholder", "أدخل كلمة المرور الحالية")
        self.fields["new_password1"].widget.attrs.setdefault("placeholder", "أدخل كلمة مرور جديدة قوية")
        self.fields["new_password2"].widget.attrs.setdefault("placeholder", "أعد إدخال كلمة المرور الجديدة")

    def _build_password_requirements(self) -> list[dict[str, str]]:
        requirements: list[dict[str, str]] = []

        for validator in get_default_password_validators():
            if isinstance(validator, MinimumLengthValidator):
                min_length = int(getattr(validator, "min_length", 8) or 8)
                self.password_min_length = min_length
                requirements.append(
                    {
                        "key": "min_length",
                        "label": f"أن تتكون من {min_length} أحرف على الأقل.",
                        "hint": "كلما زاد الطول كانت الحماية أفضل.",
                        "mode": "live",
                    }
                )
            elif isinstance(validator, UserAttributeSimilarityValidator):
                requirements.append(
                    {
                        "key": "not_similar",
                        "label": "ألا تكون قريبة من اسمك أو رقم الجوال.",
                        "hint": "تجنب أي كلمة يسهل توقعها من معلومات الحساب.",
                        "mode": "server",
                    }
                )
            elif isinstance(validator, CommonPasswordValidator):
                requirements.append(
                    {
                        "key": "not_common",
                        "label": "ألا تكون كلمة مرور شائعة أو سهلة التخمين.",
                        "hint": "مثل الكلمات الشائعة أو التسلسلات المعروفة.",
                        "mode": "server",
                    }
                )
            elif isinstance(validator, NumericPasswordValidator):
                requirements.append(
                    {
                        "key": "not_numeric",
                        "label": "ألا تتكون من أرقام فقط.",
                        "hint": "يفضل مزج الحروف مع الأرقام أو الرموز.",
                        "mode": "live",
                    }
                )

        requirements.append(
            {
                "key": "match",
                "label": "أن يتطابق تأكيد كلمة المرور مع الكلمة الجديدة.",
                "hint": "التطابق يساعد على تجنب أخطاء الكتابة قبل الحفظ.",
                "mode": "live",
            }
        )

        return requirements


def _validate_academic_year_hijri(value: str) -> str:
    """تحقق من صيغة السنة الدراسية الهجرية 1447-1448."""
    value = (value or "").strip().replace("–", "-").replace("—", "-")
    import re

    if not re.fullmatch(r"\d{4}-\d{4}", value):
        raise ValidationError("صيغة السنة الدراسية يجب أن تكون مثل 1447-1448")
    s, e = value.split("-", 1)
    if int(e) != int(s) + 1:
        raise ValidationError("السنة الدراسية يجب أن تكون مثل 1447-1448 (فرق سنة واحدة)")
    return value

# ==============================
# مساعدات داخلية للأقسام/المستخدمين
# ==============================
def _has_multi_active_schools() -> bool:
    try:
        return School.objects.filter(is_active=True).count() > 1
    except Exception:
        return False


def _teachers_for_dept(dept_slug: str, school: Optional["School"] = None):
    """
    إرجاع QuerySet للمعلمين المنتمين لقسم معيّن.
    - عبر عضوية DepartmentMembership (department ←→ teacher)

    ملاحظة: لا نعتمد على Role.slug لأن الأقسام أصبحت مخصصة لكل مدرسة ويمكن تكرار slugs.
    """
    if not dept_slug:
        return Teacher.objects.none()

    # في وضع تعدد المدارس لا نسمح بحل قسم عبر slug بدون تحديد school
    if school is None and hasattr(Department, "school") and _has_multi_active_schools():
        return Teacher.objects.none()

    dep_qs = Department.objects.filter(slug__iexact=dept_slug)
    if school is not None and hasattr(Department, "school"):
        dep_qs = dep_qs.filter(school=school)
    dep = dep_qs.first()
    if not dep:
        return Teacher.objects.none()

    base_qs = Teacher.objects.filter(is_active=True)
    if school is not None:
        base_qs = base_qs.filter(
            school_memberships__school=school,
        )

    teacher_ids = DepartmentMembership.objects.filter(department=dep).values_list("teacher_id", flat=True)
    return base_qs.filter(id__in=teacher_ids).only("id", "name").order_by("name").distinct()


def _is_teacher_in_dept(teacher: Teacher, dept_slug: str, school: Optional["School"] = None) -> bool:
    """هل المعلّم ينتمي للقسم؟"""
    if not teacher or not dept_slug:
        return False

    # في وضع تعدد المدارس لا نسمح بحل قسم عبر slug بدون تحديد school
    if school is None and hasattr(Department, "school") and _has_multi_active_schools():
        return False

    dept_slug_norm = (dept_slug or "").strip().lower()
    dep_qs = Department.objects.filter(slug__iexact=dept_slug_norm)
    if school is not None and hasattr(Department, "school"):
        dep_qs = dep_qs.filter(school=school)
    dep = dep_qs.first()
    if not dep:
        return False

    return DepartmentMembership.objects.filter(department=dep, teacher=teacher).exists()


def _is_teacher_in_department(teacher: Teacher, department: Optional[Department]) -> bool:
    """هل المعلّم ينتمي لكائن قسم محدد (بدون lookup بالـ slug)؟"""
    if not teacher or not department:
        return False

    return DepartmentMembership.objects.filter(department=department, teacher=teacher).exists()


def _compress_image_upload(f, *, max_px: int = 1600, quality: int = 85) -> InMemoryUploadedFile:
    """ضغط ملف صورة واحد قبل التخزين (يُستخدم للتقارير والتذاكر).

    - يقلّص الأبعاد القصوى إلى max_px.
    - يحاول الحفظ بصيغة WEBP، مع fallback إلى PNG/JPEG.
    """
    from PIL import Image

    img = Image.open(f)
    has_alpha = img.mode in ("RGBA", "LA", "P")
    img = img.convert("RGBA" if has_alpha else "RGB")

    if max(img.size) > max_px:
        img.thumbnail((max_px, max_px), Image.LANCZOS)

    buf = BytesIO()
    try:
        img.save(buf, format="WEBP", quality=quality, optimize=True)
        new_ext, ctype = ".webp", "image/webp"
    except Exception:
        buf = BytesIO()
        fmt = "PNG" if has_alpha else "JPEG"
        save_kwargs = {"optimize": True}
        if fmt == "JPEG":
            save_kwargs["quality"] = quality
        img.save(buf, format=fmt, **save_kwargs)
        new_ext = ".png" if has_alpha else ".jpg"
        ctype = "image/png" if has_alpha else "image/jpeg"

    buf.seek(0)
    base = os.path.splitext(getattr(f, "name", "image"))[0]
    return InMemoryUploadedFile(
        buf,
        getattr(f, "field_name", None) or "image",
        f"{base}{new_ext}",
        ctype,
        buf.getbuffer().nbytes,
        None,
    )


# ==============================
# 📌 نموذج التقرير العام
# ==============================
class ReportForm(forms.ModelForm):
    """
    يعتمد اعتمادًا كاملاً على ReportType (ديناميكي من قاعدة البيانات)
    ويستخدم قيمة code كقيمة ثابتة في الخيارات (to_field_name="code").
    """

    class Meta:
        model = Report
        fields = [
            "title",
            "report_date",
            "day_name",
            "beneficiaries_count",
            "idea",
            "category",
            "image1",
            "image2",
            "image3",
            "image4",
        ]
        widgets = {
            "title": forms.TextInput(
                attrs={
                    "class": "input",
                    "placeholder": "العنوان / البرنامج",
                    "maxlength": "255",
                    "autocomplete": "off",
                }
            ),
            "report_date": forms.DateInput(attrs={"class": "input", "type": "date"}),
            "day_name": forms.TextInput(attrs={"class": "input", "readonly": "readonly"}),
            "beneficiaries_count": forms.NumberInput(attrs={"class": "input", "min": "0", "inputmode": "numeric"}),
            "idea": forms.Textarea(attrs={"class": "textarea", "rows": 4, "placeholder": "الوصف / فكرة التقرير"}),
        }

    def __init__(self, *args, **kwargs):
        active_school = kwargs.pop("active_school", None)
        super().__init__(*args, **kwargs)

        qs = ReportType.objects.filter(is_active=True).order_by("order", "name")
        if active_school is not None and hasattr(ReportType, "school"):
            qs = qs.filter(school=active_school)

        self.fields["category"] = forms.ModelChoiceField(
            label="نوع التقرير",
            queryset=qs,
            required=True,
            empty_label="— اختر نوع التقرير —",
            to_field_name="code",
            widget=forms.Select(attrs={"class": "form-select"}),
        )

    def clean_beneficiaries_count(self):
        val = self.cleaned_data.get("beneficiaries_count")
        if val is None:
            return val
        if val < 0:
            raise ValidationError("عدد المستفيدين لا يمكن أن يكون سالبًا.")
        return val

    def clean(self):
        cleaned = super().clean()

        # ضغط الصور قبل الرفع + التحقق من الحجم بعد الضغط
        for field_name in ["image1", "image2", "image3", "image4"]:
            img = cleaned.get(field_name)
            if not img:
                continue

            ctype = (getattr(img, "content_type", "") or "").lower()
            if ctype and not ctype.startswith("image/"):
                self.add_error(field_name, "الملف يجب أن يكون صورة صالحة.")
                continue

            try:
                compressed = _compress_image_upload(img, max_px=1600, quality=85)
                cleaned[field_name] = compressed
                # تحديث self.files حتى يستخدمها model.save()
                if hasattr(self, "files"):
                    self.files[field_name] = compressed
                img = compressed
            except Exception:
                # في حال فشل الضغط نستخدم الملف كما هو مع فحص الحجم فقط
                pass

            if hasattr(img, "size") and img.size > 2 * 1024 * 1024:
                self.add_error(field_name, "حجم الصورة بعد الضغط ما زال أكبر من 2MB.")

        return cleaned

# ==============================
# 📌 نموذج إدارة المعلّم (إضافة/تعديل)
# ==============================
TEACHERS_DEPT_SLUGS = {"teachers", "معلمين", "المعلمين"}

class TeacherForm(forms.ModelForm):
    """
    إنشاء/تعديل معلّم:
    - إن كان القسم من أقسام "المعلمين" → الدور داخل القسم يقتصر على (معلم) فقط.
    - بقية الأقسام: (مسؤول القسم | موظف/معلم).
    - يضبط Teacher.role تلقائيًا.
    - ينشئ/يحدّث DepartmentMembership.
    """
    password = forms.CharField(
        label="كلمة المرور",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={
            "class": "form-control",
            "placeholder": "اتركه فارغًا للإبقاء على الحالية",
            "autocomplete": "new-password",
        }),
    )

    department = forms.ModelChoiceField(
        label="القسم",
        queryset=Department.objects.none(),
        required=True,
        empty_label="— اختر القسم —",
        to_field_name="slug",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_department"}),
    )

    membership_role = forms.ChoiceField(
        label="الدور داخل القسم",
        choices=[],  # تُضبط ديناميكيًا في __init__
        required=True,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_membership_role"}),
    )

    phone = forms.CharField(
        label="رقم الجوال",
        min_length=10, max_length=10,
        validators=[sa_phone],
        widget=forms.TextInput(attrs={
            "class": "form-control", "placeholder": "05XXXXXXXX", "maxlength": "10",
            "inputmode": "numeric", "pattern": r"0\d{9}", "autocomplete": "off"
        }),
    )
    national_id = forms.CharField(
        label="رقم الهوية الوطنية",
        min_length=10, max_length=10, required=False,
        validators=[digits10],
        widget=forms.TextInput(attrs={
            "class": "form-control", "placeholder": "رقم الهوية (10 أرقام)",
            "maxlength": "10", "inputmode": "numeric", "pattern": r"\d{10}",
            "autocomplete": "off"
        }),
    )

    class Meta:
        model = Teacher
        fields = ["name", "phone", "national_id", "is_active", "department", "membership_role"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "الاسم الكامل", "maxlength": "150"}),
        }

    ROLE_CHOICES_ALL = (
        (DepartmentMembership.OFFICER, "مسؤول القسم"),
        (DepartmentMembership.TEACHER, "موظف/معلم"),
    )
    ROLE_CHOICES_TEACHERS_ONLY = (
        (DepartmentMembership.TEACHER, "معلم"),
    )

    def _current_department_slug(self) -> Optional[str]:
        if self.is_bound:
            val = (self.data.get("department") or "").strip()
            if val:
                return val.lower()

        init_dep = (self.initial.get("department") or "")
        if init_dep:
            return str(init_dep).lower()

        dep_slug = None
        if getattr(self.instance, "pk", None):
            try:
                memb = self.instance.dept_memberships.select_related("department").first()  # type: ignore[attr-defined]
                if memb and getattr(memb.department, "slug", None):
                    dep_slug = memb.department.slug
            except Exception:
                dep_slug = None
            if not dep_slug:
                dep_slug = getattr(getattr(self.instance, "role", None), "slug", None)

        return (dep_slug or "").lower() or None

    def __init__(self, *args, **kwargs):
        active_school = kwargs.pop("active_school", None)
        super().__init__(*args, **kwargs)

        # حصر الأقسام على المدرسة النشطة فقط
        if Department is not None:
            dept_qs = Department.objects.filter(is_active=True)
            if hasattr(Department, "school"):
                if active_school is not None:
                    dept_qs = dept_qs.filter(school=active_school)
                elif _has_multi_active_schools():
                    # لا نعرض أقسامًا عشوائية عبر مدارس متعددة بدون active_school
                    dept_qs = Department.objects.none()
            self.fields["department"].queryset = dept_qs.order_by("name") if hasattr(dept_qs, "order_by") else dept_qs
        dep_slug = self._current_department_slug()
        if dep_slug and dep_slug in {s.lower() for s in TEACHERS_DEPT_SLUGS}:
            self.fields["membership_role"].choices = self.ROLE_CHOICES_TEACHERS_ONLY
            self.initial.setdefault("membership_role", DepartmentMembership.TEACHER)
        else:
            self.fields["membership_role"].choices = self.ROLE_CHOICES_ALL

    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        if nid:
            if not nid.isdigit() or len(nid) != 10:
                raise ValidationError("رقم الهوية يجب أن يتكون من 10 أرقام.")
        return nid or None

    def save(self, commit: bool = True):
        instance: Teacher = super().save(commit=False)
        new_pwd = (self.cleaned_data.get("password") or "").strip()
        dep: Optional[Department] = self.cleaned_data.get("department")

        if new_pwd:
            instance.set_password(new_pwd)
        elif self.instance and self.instance.pk:
            instance.password = self.instance.password  # إبقاء كلمة المرور

        sync_legacy_role_for_department(
            instance,
            dep,
            teacher_department_slugs=TEACHERS_DEPT_SLUGS,
            create_missing=False,
        )

        if dep and dep.slug in TEACHERS_DEPT_SLUGS:
            role_in_dept = DepartmentMembership.TEACHER
        else:
            role_in_dept = self.cleaned_data.get("membership_role") or DepartmentMembership.TEACHER

        with transaction.atomic():
            instance.save()

            if dep:
                DepartmentMembership.objects.update_or_create(
                    department=dep,
                    teacher=instance,
                    defaults={"role_type": role_in_dept},
                )

        return instance


class TeacherCreateForm(forms.ModelForm):
    """نموذج مبسّط لإنشاء معلّم فقط (بدون أي تكليفات).

    - لا يعرض/لا يطلب تحديد قسم أو دور داخل القسم.
    - لا ينشئ DepartmentMembership نهائيًا.
    - يضبط Teacher.role إلى "teacher" (إن وُجد) للتوافق مع الواجهات التراثية.
    """

    password = forms.CharField(
        label="كلمة المرور",
        required=True,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "كلمة المرور للحساب الجديد",
                "autocomplete": "new-password",
            }
        ),
    )

    phone = forms.CharField(
        label="رقم الجوال",
        min_length=10,
        max_length=10,
        validators=[sa_phone],
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "05XXXXXXXX",
                "maxlength": "10",
                "inputmode": "numeric",
                "pattern": r"0\d{9}",
                "autocomplete": "off",
            }
        ),
    )

    national_id = forms.CharField(
        label="رقم الهوية الوطنية",
        min_length=10,
        max_length=10,
        required=False,
        validators=[digits10],
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "رقم الهوية (10 أرقام)",
                "maxlength": "10",
                "inputmode": "numeric",
                "pattern": r"\d{10}",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = Teacher
        fields = ["name", "phone", "national_id", "is_active"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "الاسم الكامل", "maxlength": "150"}
            ),
        }

    job_title = forms.ChoiceField(
        label="الدور",
        required=True,
        choices=SchoolMembership.JobTitle.choices,
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="(بنفس الصلاحيات) — للاسم المعروض داخل المدرسة فقط.",
    )

    def __init__(self, *args, **kwargs):
        self._active_school = kwargs.pop("active_school", None)
        super().__init__(*args, **kwargs)
        self.fields["job_title"].choices = _school_job_title_choices(self._active_school)

    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        if nid:
            if not nid.isdigit() or len(nid) != 10:
                raise ValidationError("رقم الهوية يجب أن يتكون من 10 أرقام.")
        return nid or None

    def save(self, commit: bool = True):
        instance: Teacher = super().save(commit=False)
        pwd = (self.cleaned_data.get("password") or "").strip()
        instance.set_password(pwd)

        # توافق تراثي: Teacher.role ليس مصدر الصلاحيات، لكن بعض الشاشات القديمة
        # ما زالت تقرؤه للعرض. الكتابة الموحّدة موجودة في services_legacy_roles.
        sync_legacy_teacher_role(instance, create_missing=True)

        if commit:
            instance.save()
        return instance


class TeacherEditForm(forms.ModelForm):
    """نموذج مبسّط لتعديل بيانات المعلّم فقط (بدون أي تكليفات).

    - لا يعرض/لا يطلب قسم أو دور داخل قسم.
    - لا ينشئ/لا يحدّث DepartmentMembership.
    - كلمة المرور اختيارية: إن تُركت فارغة تبقى الحالية.
    """

    password = forms.CharField(
        label="كلمة المرور",
        required=False,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "اتركه فارغًا للإبقاء على كلمة المرور الحالية",
                "autocomplete": "new-password",
            }
        ),
    )

    phone = forms.CharField(
        label="رقم الجوال",
        min_length=10,
        max_length=10,
        validators=[sa_phone],
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "05XXXXXXXX",
                "maxlength": "10",
                "inputmode": "numeric",
                "pattern": r"0\d{9}",
                "autocomplete": "off",
            }
        ),
    )

    national_id = forms.CharField(
        label="رقم الهوية الوطنية",
        min_length=10,
        max_length=10,
        required=False,
        validators=[digits10],
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "رقم الهوية (10 أرقام)",
                "maxlength": "10",
                "inputmode": "numeric",
                "pattern": r"\d{10}",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = Teacher
        fields = ["name", "phone", "national_id", "is_active"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "الاسم الكامل", "maxlength": "150"}
            ),
        }

    job_title = forms.ChoiceField(
        label="الدور",
        required=False,
        choices=SchoolMembership.JobTitle.choices,
        widget=forms.Select(attrs={"class": "form-control"}),
        help_text="(بنفس الصلاحيات) — للاسم المعروض داخل المدرسة فقط.",
    )

    def __init__(self, *args, **kwargs):
        self._active_school = kwargs.pop("active_school", None)
        super().__init__(*args, **kwargs)
        self.fields["job_title"].choices = _school_job_title_choices(self._active_school)

        # initial job title from membership for active school (if available)
        try:
            if self._active_school is not None and self.instance and self.instance.pk:
                m = SchoolMembership.objects.filter(
                    school=self._active_school,
                    teacher=self.instance,
                    role_type=SchoolMembership.RoleType.TEACHER,
                ).only("job_title").first()
                if m is not None and getattr(m, "job_title", None):
                    self.fields["job_title"].initial = m.job_title
        except Exception:
            pass

    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        if nid:
            if not nid.isdigit() or len(nid) != 10:
                raise ValidationError("رقم الهوية يجب أن يتكون من 10 أرقام.")
        return nid or None

    def save(self, commit: bool = True):
        instance: Teacher = super().save(commit=False)
        new_pwd = (self.cleaned_data.get("password") or "").strip()

        if new_pwd:
            instance.set_password(new_pwd)
        elif self.instance and getattr(self.instance, "pk", None):
            instance.password = self.instance.password

        # توافق تراثي مركزي: شاشة التعديل القديمة ما زالت تتوقع role=teacher للعرض.
        sync_legacy_teacher_role(instance, create_missing=True)

        if commit:
            instance.save()

            try:
                if self._active_school is not None and instance.pk:
                    jt = (self.cleaned_data.get("job_title") or "").strip() or None
                    if jt:
                        SchoolMembership.objects.filter(
                            school=self._active_school,
                            teacher=instance,
                            role_type=SchoolMembership.RoleType.TEACHER,
                        ).update(job_title=jt)
            except Exception:
                pass
        return instance


class ManagerCreateForm(forms.ModelForm):
    """نموذج مبسّط لإنشاء مدير مدرسة:

    - لا يطلب تحديد قسم أو دور داخل القسم.
    - يضبط كلمة المرور للمستخدم الجديد.
    - يُستخدم مع منطق SchoolMembership في views لربط المدير بالمدارس.
    """

    password = forms.CharField(
        label="كلمة المرور",
        required=True,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "كلمة المرور للحساب الجديد",
                "autocomplete": "new-password",
            }
        ),
    )

    phone = forms.CharField(
        label="رقم الجوال",
        min_length=10,
        max_length=10,
        validators=[sa_phone],
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "05XXXXXXXX",
                "maxlength": "10",
                "inputmode": "numeric",
                "pattern": r"0\d{9}",
                "autocomplete": "off",
            }
        ),
    )

    email = forms.EmailField(
        label="البريد الإلكتروني",
        required=False,
        widget=forms.EmailInput(
            attrs={
                "class": "form-control",
                "placeholder": "manager@school.edu.sa",
                "autocomplete": "email",
            }
        ),
    )

    national_id = forms.CharField(
        label="رقم الهوية الوطنية",
        min_length=10,
        max_length=10,
        required=False,
        validators=[digits10],
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "رقم الهوية (10 أرقام)",
                "maxlength": "10",
                "inputmode": "numeric",
                "pattern": r"\d{10}",
                "autocomplete": "off",
            }
        ),
    )

    class Meta:
        model = Teacher
        fields = ["name", "phone", "email", "national_id", "is_active"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "الاسم الكامل", "maxlength": "150"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.fields["password"].required = False
            self.fields["password"].widget.attrs["placeholder"] = "اتركها فارغة للإبقاء على الحالية"

    def save(self, commit=True):
        instance = super().save(commit=False)
        password = self.cleaned_data.get("password")
        if password:
            instance.set_password(password)
        if commit:
            instance.save()
        return instance


class PlatformAdminCreateForm(forms.ModelForm):
    """إنشاء حساب مشرف عام (عرض + تواصل) مع نطاق (Scope)."""

    password = forms.CharField(
        label="كلمة المرور",
        required=True,
        strip=False,
        widget=forms.PasswordInput(
            attrs={
                "class": "form-control",
                "placeholder": "كلمة المرور للحساب الجديد",
                "autocomplete": "new-password",
            }
        ),
    )

    gender_scope = forms.ChoiceField(
        label="نطاق بنين/بنات",
        choices=[("all", "الجميع"), ("boys", "بنين"), ("girls", "بنات")],
        required=True,
        widget=forms.Select(attrs={"class": "form-control"}),
        initial="all",
    )

    role = forms.ModelChoiceField(
        label="دور المشرف",
        required=True,
        queryset=None,
        widget=forms.Select(attrs={"class": "form-control"}),
    )

    cities = forms.CharField(
        label="مدن (اختياري)",
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "مثال: الرياض, جدة, الدمام",
            }
        ),
    )

    allowed_schools = forms.ModelMultipleChoiceField(
        label="مدارس محددة (اختياري)",
        required=False,
        queryset=School.objects.filter(is_active=True).order_by("name"),
        widget=forms.SelectMultiple(attrs={"class": "form-control"}),
    )

    class Meta:
        model = Teacher
        fields = ["name", "phone", "is_active"]
        widgets = {
            "name": forms.TextInput(
                attrs={"class": "form-control", "placeholder": "الاسم الكامل", "maxlength": "150"}
            ),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # أدوار مشرفي المنصة (قابلة للإدارة من Django Admin)
        try:
            from .models import PlatformAdminRole

            roles_qs = PlatformAdminRole.objects.filter(is_active=True).order_by("order", "name", "id")
            self.fields["role"].queryset = roles_qs
            # Default role: "general" if exists, else first active
            default_role = roles_qs.filter(slug="general").first() or roles_qs.first()
            if default_role is not None:
                self.fields["role"].initial = default_role
        except Exception:
            # إذا لم تكن الهجرة مطبقة بعد، نترك الحقل كما هو (قد يُسبب خطأ عرضي)
            pass

        # عند التعديل: كلمة المرور اختيارية
        if getattr(self.instance, "pk", None):
            self.fields["password"].required = False
            self.fields["password"].widget.attrs["placeholder"] = "اتركه فارغًا للإبقاء على كلمة المرور الحالية"

            # تعبئة نطاق الصلاحيات من PlatformAdminScope (إن وجد)
            try:
                from .models import PlatformAdminScope

                scope = (
                    PlatformAdminScope.objects.filter(admin=self.instance)
                    .prefetch_related("allowed_schools")
                    .first()
                )
                if scope is not None:
                    # الدور
                    try:
                        role_obj = getattr(scope, "role", None)
                        if role_obj is not None:
                            self.initial.setdefault("role", role_obj)
                    except Exception:
                        pass
                    self.initial.setdefault("gender_scope", scope.gender_scope)
                    try:
                        self.initial.setdefault("cities", ", ".join(list(scope.allowed_cities or [])))
                    except Exception:
                        self.initial.setdefault("cities", "")
                    self.initial.setdefault("allowed_schools", scope.allowed_schools.all())
            except Exception:
                pass

    def save(self, commit: bool = True):
        instance: Teacher = super().save(commit=False)
        pwd = (self.cleaned_data.get("password") or "").strip()
        if pwd:
            instance.set_password(pwd)
        instance.is_platform_admin = True
        sync_legacy_teacher_role(instance, create_missing=False)
        if commit:
            instance.save()
        return instance


class PlatformSchoolNotificationForm(forms.Form):
    title = forms.CharField(
        label="العنوان",
        required=False,
        max_length=120,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "عنوان مختصر (اختياري)"}),
    )
    message = forms.CharField(
        label="الرسالة",
        required=True,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 5, "placeholder": "اكتب نص الإشعار هنا…"}),
    )
    is_important = forms.BooleanField(label="مهم؟", required=False)


class PrivateCommentForm(forms.Form):
    body = forms.CharField(
        label="تعليق للمعلّم",
        required=True,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "اكتب تعليقًا يظهر للمعلم فقط…"}),
    )

# ==============================
# 📌 تذاكر — إنشاء/إجراءات/ملاحظات
# ==============================

# ==== داخل reports/forms.py (استبدل تعريف TicketCreateForm فقط بهذا) ====
from io import BytesIO
from django.core.files.uploadedfile import InMemoryUploadedFile

class MultiImageInput(forms.ClearableFileInput):
    """عنصر إدخال يسمح باختيار عدة صور."""
    allow_multiple_selected = True

class MultiFileField(forms.FileField):
    """
    حقل ملفات متعدد:
    - يقبل [] بدون أخطاء عندما لا تُرفع صور.
    - يعيد list[UploadedFile] عند وجود صور.
    """
    def to_python(self, data):
        if not data:
            return []
        # في حال مر ملف مفرد من متصفح قديم
        if not isinstance(data, (list, tuple)):
            return [data]
        return list(data)

    def validate(self, data):
        # لا نريد رسالة "لم يتم إرسال ملف..." عند عدم وجود صور
        if self.required and not data:
            raise forms.ValidationError(self.error_messages["required"], code="required")
        # أي تحقق إضافي خاص بالحقل نفسه يمكن وضعه هنا (نحن نتحقق لاحقًا في form.clean)

class TicketCreateForm(forms.ModelForm):
    """
    إنشاء تذكرة جديدة مع رفع حتى 4 صور (JPG/PNG/WebP) بحجم أقصى 5MB للصورة.
    - department يُرسل slug (to_field_name="slug")
    - recipients يُبنى ديناميكيًا (اختيار متعدد)
    - images اختيارية ومتعددة (MultiFileField)
    """

    department = forms.ModelChoiceField(
        label="القسم",
        queryset=Department.objects.none(),
        required=True,
        empty_label="— اختر القسم —",
        to_field_name="slug",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_department"}),
    )

    recipients = forms.ModelMultipleChoiceField(
        label="المستلمون",
        queryset=Teacher.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple(attrs={"id": "id_recipients"}),
        help_text="يمكن اختيار أكثر من مستلم واحد.",
    )

    # ✅ حقل متعدد ينسجم مع الـ multiple في القالب
    images = MultiFileField(
        label="الصور (حتى 4)",
        required=False,
        widget=MultiImageInput(attrs={"accept": "image/*", "multiple": True, "id": "id_images"}),
        help_text="حتى 4 صور، ‎JPG/PNG/WebP، الحد الأقصى لكل صورة 5MB.",
    )

    class Meta:
        model = Ticket
        fields = ["department", "recipients", "title", "body"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "input", "placeholder": "عنوان الطلب", "maxlength": "255", "autocomplete": "off"
            }),
            "body": forms.Textarea(attrs={"class": "textarea", "rows": 4, "placeholder": "تفاصيل الطلب"}),
        }

    def __init__(self, *args, **kwargs):
        kwargs.pop("user", None)  # يُمرَّر في save
        active_school = kwargs.pop("active_school", None)
        super().__init__(*args, **kwargs)

        self.active_school = active_school

        # عزل الأقسام حسب المدرسة النشطة
        if Department is not None:
            dept_qs = Department.objects.filter(is_active=True)
            if hasattr(Department, "school"):
                if active_school is not None:
                    dept_qs = dept_qs.filter(school=active_school)
                elif _has_multi_active_schools():
                    dept_qs = Department.objects.none()
            self.fields["department"].queryset = dept_qs.order_by("name") if hasattr(dept_qs, "order_by") else dept_qs

        # تأكيد اختياريّة الصور (تحصين إضافي)
        self.fields["images"].required = False

        # بناء قائمة المستلمين حسب القسم
        dept_value = (self.data.get("department") or "").strip() if self.is_bound \
            else getattr(getattr(self.instance, "department", None), "slug", "") or ""
        base_qs = _teachers_for_dept(dept_value, active_school) if dept_value else Teacher.objects.none()
        self.fields["recipients"].queryset = base_qs

        # سنخزن النسخ المضغوطة بعد التحقق
        self._compressed_images: List[InMemoryUploadedFile] = []

    # ضغط صورة مع fallback
    def _compress_image(self, f, *, max_px=1600, quality=85) -> InMemoryUploadedFile:
        from PIL import Image
        img = Image.open(f)
        has_alpha = img.mode in ("RGBA", "LA", "P")
        img = img.convert("RGBA" if has_alpha else "RGB")
        if max(img.size) > max_px:
            img.thumbnail((max_px, max_px), Image.LANCZOS)

        buf = BytesIO()
        try:
            img.save(buf, format="WEBP", quality=quality, optimize=True)
            new_ext, ctype = ".webp", "image/webp"
        except Exception:
            buf = BytesIO()
            fmt = "PNG" if has_alpha else "JPEG"
            save_kwargs = {"optimize": True}
            if fmt == "JPEG":
                save_kwargs["quality"] = quality
            img.save(buf, format=fmt, **save_kwargs)
            new_ext = ".png" if has_alpha else ".jpg"
            ctype = "image/png" if has_alpha else "image/jpeg"
        buf.seek(0)

        base = os.path.splitext(getattr(f, "name", "image"))[0]
        return InMemoryUploadedFile(buf, "images", f"{base}{new_ext}", ctype, buf.getbuffer().nbytes, None)

    def clean(self):
        cleaned = super().clean()

        dept: Optional[Department] = cleaned.get("department")
        recipients = list(cleaned.get("recipients") or [])

        if not dept:
            self.add_error("department", "الرجاء اختيار القسم.")

        # المستلمون: نطلب على الأقل مستلمًا واحدًا إذا وُجدت خيارات
        if dept:
            qs = self.fields["recipients"].queryset
            if qs.count() > 0 and not recipients:
                self.add_error("recipients", "يرجى اختيار مستلم واحد على الأقل.")

            # تحصين: كل المستلمين يجب أن يكونوا ضمن QuerySet القسم
            if recipients:
                allowed_ids = set(qs.values_list("id", flat=True)) if hasattr(qs, "values_list") else set()
                bad = [t for t in recipients if getattr(t, "id", None) not in allowed_ids]
                if bad:
                    self.add_error("recipients", "يوجد مستلم/مستلمون لا ينتمون إلى هذا القسم.")

        # الآن images هي list[UploadedFile] قادمة من الحقل نفسه
        files = self.cleaned_data.get("images") or []
        if files:
            if len(files) > 4:
                self.add_error("images", "الحد الأقصى 4 صور.")
            ok_ext = {".jpg", ".jpeg", ".png", ".webp"}
            for f in files:
                name = (getattr(f, "name", "") or "").lower()
                ext = os.path.splitext(name)[1]
                ctype = (getattr(f, "content_type", "") or "").lower()

                if getattr(f, "size", 0) > 5 * 1024 * 1024:
                    self.add_error("images", f"({name}) حجم الصورة أكبر من 5MB.")
                    break
                if not (ctype.startswith("image/") and ext in ok_ext):
                    self.add_error("images", f"({name}) يُسمح فقط بصور JPG/PNG/WebP.")
                    break

            if not self.errors.get("images"):
                self._compressed_images = [self._compress_image(f) for f in files]

        return cleaned

    def save(self, commit: bool = True, user: Optional[Teacher] = None):
        obj: Ticket = super().save(commit=False)

        # تعيين المُنشئ لأول مرة
        if user is not None and not obj.pk:
            obj.creator = user

        # حالة افتراضية إن وُجدت في الموديل
        if not getattr(obj, "status", None):
            try:
                obj.status = Ticket.Status.OPEN  # type: ignore[attr-defined]
            except Exception:
                pass

        # تعيين assignee كمرجع/مسؤول رئيسي للتوافق الخلفي (أول مستلم)
        try:
            recipients = list(self.cleaned_data.get("recipients") or [])
        except Exception:
            recipients = []
        if recipients:
            obj.assignee = recipients[0]

        if commit:
            obj.save()

            # حفظ المستلمين (ManyToMany through)
            if recipients:
                try:
                    obj.recipients.set(recipients)
                except Exception:
                    # fallback آمن عبر through model (في حال قيود بيئية)
                    from .models import TicketRecipient
                    TicketRecipient.objects.bulk_create(
                        [TicketRecipient(ticket=obj, teacher=t) for t in recipients],
                        ignore_conflicts=True,
                    )

            # حفظ الصور (إن وُجدت)
            if self._compressed_images:
                from .models import TicketImage
                for f in self._compressed_images:
                    TicketImage.objects.create(ticket=obj, image=f)

        return obj

class TicketActionForm(forms.Form):
    status = forms.ChoiceField(
        choices=Ticket.Status.choices,
        required=False,
        widget=forms.Select(attrs={"class": "input"}),
        label="تغيير الحالة",
    )
    note = forms.CharField(
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "class": "textarea", "placeholder": "اكتب ملاحظة (تظهر للمرسل)"}),
        label="ملاحظة",
    )

    def clean(self):
        cleaned = super().clean()
        if not cleaned.get("status") and not (cleaned.get("note") or "").strip():
            raise forms.ValidationError("أدخل ملاحظة أو غيّر الحالة.")
        return cleaned

class TicketNoteForm(forms.ModelForm):
    class Meta:
        model = TicketNote
        fields = ["body", "is_public"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 3, "class": "textarea", "placeholder": "أضف ملاحظة"}),
        }


class TicketNoteEditForm(forms.ModelForm):
    class Meta:
        model = TicketNote
        fields = ["body"]
        widgets = {
            "body": forms.Textarea(attrs={"rows": 4, "class": "textarea", "placeholder": "عدّل ملاحظتك"}),
        }

# ==============================
# 📌 نموذج الطلب التراثي (اختياري)
# ==============================
if HAS_REQUEST_TICKET and RequestTicket is not None:

    class RequestTicketForm(forms.ModelForm):
        department = forms.ChoiceField(
            choices=[],
            required=True,
            widget=forms.Select(attrs={"class": "form-select"}),
            label="القسم",
        )
        assignee = forms.ModelChoiceField(
            queryset=Teacher.objects.none(),
            required=False,
            widget=forms.Select(attrs={"class": "form-select"}),
            label="المستلم",
        )

        class Meta:
            model = RequestTicket
            fields = ["department", "assignee", "title", "body", "attachment"]
            widgets = {
                "title": forms.TextInput(attrs={"class": "input", "placeholder": "عنوان مختصر", "maxlength": "200"}),
                "body": forms.Textarea(attrs={"class": "textarea", "rows": 5, "placeholder": "اكتب تفاصيل الطلب..."}),
            }

        def __init__(self, *args, **kwargs):
            kwargs.pop("user", None)
            active_school = kwargs.pop("active_school", None)
            super().__init__(*args, **kwargs)

            self.active_school = active_school

            # مصادر الاختيارات لقسم تراثي
            choices: List[Tuple[str, str]] = []
            try:
                field = RequestTicket._meta.get_field("department")
                model_choices = list(getattr(field, "choices", []))
                choices = [(v, l) for (v, l) in model_choices if v not in ("", None)]
            except Exception:
                if REQUEST_DEPARTMENTS:
                    choices = list(REQUEST_DEPARTMENTS)
            self.fields["department"].choices = [("", "— اختر القسم —")] + choices

            # إعداد assignee بحسب القسم
            if self.is_bound:
                dept_value = (self.data.get("department") or "").strip()
            elif getattr(self.instance, "pk", None):
                dept_value = getattr(self.instance, "department", None)
            else:
                dept_value = ""

            if dept_value:
                qs = _teachers_for_dept(dept_value, self.active_school)
                self.fields["assignee"].queryset = qs
                if qs.count() == 1 and not self.is_bound and not getattr(self.instance, "assignee_id", None):
                    self.initial["assignee"] = qs.first().pk
            else:
                self.fields["assignee"].queryset = Teacher.objects.none()

        def clean(self):
            cleaned = super().clean()
            dept = (cleaned.get("department") or "").strip()
            assignee: Optional[Teacher] = cleaned.get("assignee")
            if dept:
                qs = _teachers_for_dept(dept, getattr(self, "active_school", None))
                if qs.count() > 1 and assignee is None:
                    self.add_error("assignee", "يرجى اختيار الموظّف المستلم.")
                if assignee and not qs.filter(id=assignee.id).exists():
                    self.add_error("assignee", "الموظّف المختار لا ينتمي إلى هذا القسم.")
            return cleaned

else:
    # في حال إزالة النماذج التراثية من المشروع
    class RequestTicketForm(forms.Form):
        title = forms.CharField(disabled=True)
        body = forms.CharField(widget=forms.Textarea, disabled=True)

        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.add_error(None, "نموذج الطلب التراثي غير مفعّل في هذا المشروع.")

# ==============================
# 📌 نموذج إدارة القسم (اختيار أنواع التقارير)
# ==============================
class DepartmentForm(forms.ModelForm):
    """
    نموذج إدارة القسم مع اختيار أنواع التقارير المسموح بها لهذا القسم.
    سيُزامن الدور تلقائيًا عبر إشعار m2m في models.py.
    """
    reporttypes = forms.ModelMultipleChoiceField(
        label="أنواع التقارير المرتبطة",
        queryset=ReportType.objects.filter(is_active=True).order_by("order", "name"),
        required=False,
        widget=forms.SelectMultiple(
            attrs={
                "class": "form-select",
                "size": "8",
                "aria-label": "اختر نوع/أنواع التقارير للقسم",
            }
        ),
        help_text="المسؤولون عن هذا القسم سيشاهدون التقارير من هذه الأنواع فقط.",
    )

    class Meta:
        model = Department
        fields = ["name", "slug", "is_active", "reporttypes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "maxlength": "120"}),
            "slug": forms.TextInput(attrs={"class": "form-control", "maxlength": "64"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def _slugify_english(self, text: str) -> str:
        # توليد slug ASCII (إنجليزي) حتى لو كان الاسم عربيًا.
        try:
            from unidecode import unidecode  # type: ignore

            text = unidecode(text or "")
        except Exception:
            # fallback: بدون تحويل
            pass
        return slugify(text or "", allow_unicode=False)

    def clean_slug(self):
        slug = (self.cleaned_data.get("slug") or "").strip().lower()
        if not slug:
            slug = self._slugify_english(self.cleaned_data.get("name") or "")
        # fallback في حال كان الاسم غير قابل للتحويل
        if not slug:
            slug = "dept"

        # في وضع تعدد المدارس لا نسمح بفحص/إنشاء slug بدون مدرسة نشطة محددة
        active_school = getattr(self, "active_school", None)
        if active_school is None and hasattr(Department, "school") and _has_multi_active_schools():
            raise forms.ValidationError("فضلاً اختر مدرسة أولاً.")

        qs = Department.objects.filter(slug=slug)
        # حصر فحص التعارض داخل المدرسة النشطة عند توفرها
        if active_school is not None and hasattr(Department, "school"):
            qs = qs.filter(school=active_school)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("المعرّف (slug) مستخدم مسبقًا لقسم آخر.")
        return slug

    def __init__(self, *args, **kwargs):
        active_school = kwargs.pop("active_school", None)
        super().__init__(*args, **kwargs)

        self.active_school = active_school

        # حصر أنواع التقارير على المدرسة النشطة
        if ReportType is not None:
            rt_qs = ReportType.objects.filter(is_active=True).order_by("order", "name")
            if active_school is not None and hasattr(ReportType, "school"):
                rt_qs = rt_qs.filter(school=active_school)
            self.fields["reporttypes"].queryset = rt_qs


class ReportTypeForm(forms.ModelForm):
    """Report type form with an internal auto-generated code."""

    class Meta:
        model = ReportType
        fields = ["name", "description", "order", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "smart-input", "maxlength": "120"}),
            "description": forms.Textarea(attrs={"class": "smart-input", "rows": 6}),
            "order": forms.NumberInput(attrs={"class": "smart-input", "min": "0", "inputmode": "numeric"}),
            "is_active": forms.CheckboxInput(),
        }

    def __init__(self, *args, **kwargs):
        self.active_school = kwargs.pop("active_school", None)
        super().__init__(*args, **kwargs)

    def _slugify_english(self, text: str) -> str:
        try:
            from unidecode import unidecode  # type: ignore

            text = unidecode(text or "")
        except Exception:
            pass
        return slugify(text or "", allow_unicode=False)

    def _generate_unique_code(self, name: str) -> str:
        max_length = ReportType._meta.get_field("code").max_length
        base_code = self._slugify_english((name or "").strip()) or "report-type"
        base_code = base_code[:max_length]

        school = self.active_school
        if school is None and getattr(self.instance, "school_id", None):
            school = getattr(self.instance, "school", None)

        qs = ReportType.objects.all()
        if school is not None and hasattr(ReportType, "school"):
            qs = qs.filter(school=school)
        elif hasattr(ReportType, "school"):
            qs = qs.filter(school__isnull=True)

        if getattr(self.instance, "pk", None):
            qs = qs.exclude(pk=self.instance.pk)

        candidate = base_code
        suffix_index = 2
        while qs.filter(code=candidate).exists():
            suffix = f"-{suffix_index}"
            prefix_max = max_length - len(suffix)
            candidate = f"{base_code[:prefix_max]}{suffix}"
            suffix_index += 1

        return candidate

    def save(self, commit: bool = True):
        instance = super().save(commit=False)
        if hasattr(instance, "school") and self.active_school is not None:
            instance.school = self.active_school
        instance.code = self._generate_unique_code(self.cleaned_data.get("name") or instance.name or "")
        if commit:
            instance.save()
        return instance

# ==============================
# 📌 إنشاء إشعار
# ==============================
class NotificationCreateForm(forms.Form):
    title = forms.CharField(max_length=120, required=False, label="عنوان (اختياري)")
    message = forms.CharField(widget=forms.Textarea(attrs={"rows":5}), label="نص الإشعار")
    is_important = forms.BooleanField(required=False, initial=False, label="مهم")
    expires_at = forms.DateTimeField(required=False, label="ينتهي في (اختياري)",
                                     widget=forms.DateTimeInput(attrs={"type":"datetime-local"}))

    attachment = forms.FileField(
        required=False,
        label="مرفق (اختياري)",
        help_text="PDF/صور (حد أقصى 5MB).",
        validators=[validate_circular_attachment_file],
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".pdf,.jpg,.jpeg,.png",
            }
        ),
    )

    # ==============================
    # التعميمات والتوقيع الإلزامي
    # ==============================
    requires_signature = forms.BooleanField(
        required=False,
        initial=False,
        label="يتطلب توقيع إلزامي (تعميم)",
        help_text="عند تفعيل هذا الخيار سيُطلب من المستلم إدخال جواله المسجل + الإقرار قبل اعتماد التوقيع.",
    )
    signature_deadline_at = forms.DateTimeField(
        required=False,
        label="آخر موعد للتوقيع (اختياري)",
        widget=forms.DateTimeInput(attrs={"type": "datetime-local"}),
    )
    signature_ack_text = forms.CharField(
        required=False,
        label="نص الإقرار (اختياري)",
        widget=forms.Textarea(attrs={"rows": 3}),
        initial="أقرّ بأنني اطلعت على هذا التعميم وفهمت ما ورد فيه وأتعهد بالالتزام به.",
        help_text="سيظهر نص الإقرار للمستلم داخل صفحة التوقيع.",
    )
    audience_scope = forms.ChoiceField(
        label="نطاق الإرسال",
        required=False,
        choices=(
            ("school", "مدرسة معيّنة"),
            ("all", "كل المدارس"),
        ),
        initial="school",
        help_text="للمشرف العام فقط: اختر ما إذا كان الإشعار موجهاً لمدرسة واحدة أو لكل المدارس.",
    )
    target_school = forms.ModelChoiceField(
        queryset=School.objects.none(),
        required=False,
        label="المدرسة المستهدفة",
        help_text="اختر المدرسة التي سيتم إرسال الإشعار لمستخدميها.",
    )
    target_department = forms.ModelChoiceField(
        queryset=Department.objects.none(),
        required=False,
        label="إرسال إلى قسم كامل",
        help_text="اختر قسماً لإرسال الإشعار لجميع منسوبيه. إذا اخترت مستلمين من القائمة أدناه فسيتم الإرسال لهم فقط (حتى لو كان القسم محدداً).",
    )
    teachers = forms.ModelMultipleChoiceField(
        queryset=Teacher.objects.none(),
        required=False,
        label="المستلمون (يمكن اختيار أكثر من معلم)",
        help_text="اختيار المستلمين يدويًا يجعل الإرسال يقتصر عليهم فقط.",
        widget=forms.CheckboxSelectMultiple()
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        active_school = kwargs.pop("active_school", None)
        mode = (kwargs.pop("mode", None) or "notification").strip().lower()
        super().__init__(*args, **kwargs)

        self.user = user
        self.active_school = active_school
        self.mode = mode if mode in {"notification", "circular"} else "notification"
        is_circular = self.mode == "circular"

        # المرفقات للتعاميم فقط
        if not is_circular:
            self.fields.pop("attachment", None)

        from .permissions import is_platform_admin, platform_allowed_schools_qs

        is_superuser = bool(getattr(user, "is_superuser", False))
        is_platform = bool(is_platform_admin(user)) and not is_superuser
        school_gender = (getattr(active_school, "gender", "") or "").strip().lower()
        girls_value = str(getattr(getattr(School, "Gender", None), "GIRLS", "girls")).strip().lower()
        is_girls_school = school_gender == girls_value
        teacher_singular = "معلمة" if is_girls_school else "معلم"
        teachers_plural = "المعلمات" if is_girls_school else "المعلمون"
        teachers_obj = "المعلمات" if is_girls_school else "المعلمين"
        
        # التحقق مما إذا كان المستخدم مديراً ضمن المدرسة النشطة (عزل مدارس)
        try:
            from .views._helpers import _is_manager_in_school
            is_manager = bool(_is_manager_in_school(user, active_school))
        except Exception:
            is_manager = False

        # إضافة حقل "إرسال للكل" للمشرف العام في وضع التعميمات
        if is_circular and is_platform:
            self.fields["send_to_all_managers"] = forms.BooleanField(
                label="إرسال لجميع مدراء المدارس ضمن نطاقي",
                required=False,
                initial=False,
                widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
                help_text="اختر هذا الخيار لإرسال التعميم لجميع مدراء المدارس ضمن نطاقك، أو اترك الخيار فارغًا واختر مدراء معينين من القائمة.",
            )

        # إعداد حقول نطاق الإرسال/المدرسة حسب نوع المستخدم
        if is_superuser or is_platform:
            if is_superuser:
                self.fields["target_school"].queryset = School.objects.filter(is_active=True).order_by("name")
            else:
                self.fields["target_school"].queryset = platform_allowed_schools_qs(user).order_by("name")

            # الأقسام: ليست ضمن نطاق ميزة التعاميم/المشرف العام، فنبقيها للسوبر فقط
            if is_superuser and "target_department" in self.fields:
                self.fields["target_department"].queryset = Department.objects.filter(is_active=True).order_by("name")
            else:
                self.fields.pop("target_department", None)
        else:
            # لا يحتاج المدير/الضابط لاختيار النطاق أو المدرسة؛ نستخدم المدرسة النشطة تلقائياً
            self.fields.pop("audience_scope", None)
            self.fields.pop("target_school", None)

            # جلب أقسام المدرسة النشطة فقط (للمدير فقط حسب الطلب)
            if is_manager and active_school:
                self.fields["target_department"].queryset = Department.objects.filter(
                    models.Q(school=active_school),
                    is_active=True
                ).order_by("name")
            else:
                self.fields.pop("target_department", None)

        # في وضع التعميم: لا علاقة للأقسام بالتوجيه حسب متطلبات المنتج
        if is_circular:
            self.fields.pop("target_department", None)

            # التعميم دائمًا يتطلب توقيعًا (والـ view يفرضه كذلك)
            if "requires_signature" in self.fields:
                try:
                    self.fields["requires_signature"].initial = True
                except Exception:
                    pass

        qs = Teacher.objects.filter(is_active=True).order_by("name")

        # ==============================
        # فصل التعميمات 100% (المستلمون)
        # ==============================
        if is_circular:
            # مدير النظام (superuser): يرسل التعميمات لمدراء المدارس فقط
            if is_superuser:
                qs = qs.filter(
                    school_memberships__role_type=SchoolMembership.RoleType.MANAGER,
                    school_memberships__is_active=True,
                    school_memberships__school__is_active=True,
                ).distinct()

                # إعادة تسمية الحقل ليتوافق مع الواقع (مدراء مدارس)
                if "teachers" in self.fields:
                    self.fields["teachers"].label = "مدراء المدارس (يمكن اختيار أكثر من مدير)"
                    self.fields["teachers"].help_text = "يمكنك ترك الاختيار فارغًا لإرسال التعميم لجميع مدراء المدارس ضمن النطاق المحدد."

                # لو اختار السوبر مدرسة محددة، قيد المدراء بهذه المدرسة
                scope_val = (self.data.get("audience_scope") or self.initial.get("audience_scope") or "").strip()
                school_id = self.data.get("target_school") or self.initial.get("target_school")
                if (not scope_val or scope_val == "school") and school_id:
                    try:
                        qs = qs.filter(school_memberships__school_id=int(school_id)).distinct()
                    except ValueError:
                        pass

            # المشرف العام: يرسل التعميمات لمدراء المدارس ضمن نطاقه فقط
            elif is_platform:
                allowed_schools_qs = platform_allowed_schools_qs(user)
                qs = qs.filter(
                    school_memberships__role_type=SchoolMembership.RoleType.MANAGER,
                    school_memberships__is_active=True,
                    school_memberships__school__is_active=True,
                    school_memberships__school__in=allowed_schools_qs,
                ).distinct()

                if "teachers" in self.fields:
                    self.fields["teachers"].label = "مدراء معينين (اختياري)"
                    self.fields["teachers"].help_text = "اختر مدراء معينين فقط، أو فعّل خيار 'إرسال للكل' أعلاه."
                    self.fields["teachers"].required = False

                scope_val = (self.data.get("audience_scope") or self.initial.get("audience_scope") or "").strip()
                school_id = self.data.get("target_school") or self.initial.get("target_school")
                if (not scope_val or scope_val == "school") and school_id:
                    try:
                        qs = qs.filter(school_memberships__school_id=int(school_id)).distinct()
                    except ValueError:
                        pass

            # مدير المدرسة: يرسل التعميمات للمعلمين ضمن مدرسته فقط
            else:
                if active_school is not None:
                    qs = qs.filter(
                        school_memberships__school=active_school,
                        school_memberships__is_active=True,
                        school_memberships__role_type=SchoolMembership.RoleType.TEACHER,
                    ).exclude(
                        is_platform_admin=True,
                    ).distinct()
                else:
                    qs = qs.none()

                if "teachers" in self.fields:
                    self.fields["teachers"].label = f"{teachers_plural} (يمكن اختيار {teacher_singular} أو أكثر)"
                    self.fields["teachers"].help_text = f"يجب تحديد مستلم واحد على الأقل قبل إرسال التعميم إلى {teachers_obj}."

            self.fields["teachers"].queryset = qs
            return

        # تقليص القائمة حسب الأقسام التي يديرها المستخدم (للضباط)
        try:
            role_slug = getattr(getattr(user, "role", None), "slug", None)
            if role_slug and role_slug not in (None, "manager"):
                # عزل: اجلب أقسام الضابط داخل المدرسة النشطة فقط
                try:
                    from .permissions import get_officer_departments
                    officer_depts = get_officer_departments(user, active_school=active_school)
                    codes = [d.slug for d in officer_depts if getattr(d, "slug", None)]
                except Exception:
                    codes = []
                if codes:
                    qs = qs.filter(
                        models.Q(role__slug__in=codes)
                        | models.Q(dept_memberships__department__slug__in=codes)
                    ).distinct()
        except Exception:
            pass

        # تقليص حسب المدرسة النشطة للمدير/الضابط
        if active_school is not None:
            qs = qs.filter(
                school_memberships__school=active_school,
            ).distinct()

        # للمشرف العام: لو اختار "مدرسة معيّنة" في الطلب، نقيّد القائمة بهذه المدرسة
        if is_superuser:
            scope_val = (self.data.get("audience_scope") or self.initial.get("audience_scope") or "").strip()
            school_id = self.data.get("target_school") or self.initial.get("target_school")
            if (not scope_val or scope_val == "school") and school_id:
                try:
                    qs = qs.filter(
                        school_memberships__school_id=int(school_id),
                    ).distinct()
                except ValueError:
                    pass

        self.fields["teachers"].queryset = qs

    def clean(self):
        cleaned = super().clean()
        user = getattr(self, "user", None)
        try:
            from .permissions import is_platform_admin
            is_superuser = bool(getattr(user, "is_superuser", False))
            is_platform = bool(is_platform_admin(user)) and not is_superuser
        except Exception:
            is_superuser = bool(getattr(user, "is_superuser", False))
            is_platform = False

        mode = getattr(self, "mode", "notification") or "notification"
        is_circular = mode == "circular"

        if is_superuser or is_platform:
            scope = cleaned.get("audience_scope") or "school"
            target_school = cleaned.get("target_school")
            if scope == "school" and not target_school:
                # للتعاميم: المشرف العام قد يرسل لمدراء من عدة مدارس ضمن نطاقه،
                # لذلك لا نجبره على اختيار مدرسة واحدة؛ نعاملها كنطاق "كل المدارس" ضمن النطاق.
                if is_platform and is_circular:
                    cleaned["audience_scope"] = "all"
                else:
                    raise ValidationError("الرجاء اختيار مدرسة مستهدفة أو تغيير النطاق إلى \"كل المدارس\".")

        # التحقق من اختيار المشرف العام: إما إرسال للكل أو اختيار مدراء معينين
        if is_circular and is_platform:
            send_to_all = cleaned.get("send_to_all_managers", False)
            teachers = cleaned.get("teachers", [])
            if not send_to_all and not teachers:
                raise ValidationError("يرجى اختيار مدراء معينين أو تفعيل خيار 'إرسال لجميع مدراء المدارس ضمن نطاقي'.")

        # التعميمات داخل المدرسة: مدير المدرسة يجب أن يحدد مستلمين صراحةً.
        if is_circular and not is_superuser and not is_platform:
            selected_teachers = cleaned.get("teachers")
            if not selected_teachers:
                self.add_error("teachers", "لا يمكن إرسال التعميم والمستلمون = 0. يرجى تحديد المستلمين أولاً.")

        # للإشعارات العادية (داخل المدرسة): لا نسمح بالإرسال بدون تحديد مستلمين.
        # يمكن التحديد إما عبر اختيار معلمين بشكل مباشر أو اختيار قسم كامل.
        if not is_circular and not (is_superuser or is_platform):
            selected_teachers = cleaned.get("teachers")
            target_department = cleaned.get("target_department")
            if not selected_teachers and not target_department:
                raise ValidationError("يرجى تحديد المستلمين (اختيار معلم/معلمة أو قسم) قبل إرسال الإشعار.")
            if target_department and not selected_teachers:
                dept_recipients_qs = Teacher.objects.filter(
                    is_active=True,
                    dept_memberships__department=target_department,
                )
                active_school = getattr(self, "active_school", None)
                if active_school is not None:
                    dept_recipients_qs = dept_recipients_qs.filter(
                        school_memberships__school=active_school,
                        school_memberships__is_active=True,
                    )
                if not dept_recipients_qs.distinct().exists():
                    self.add_error(
                        "target_department",
                        "القسم المحدد لا يحتوي على مستلمين نشطين حاليًا. يرجى اختيار مستلمين يدويًا.",
                    )
        return cleaned

    def save(self, creator, default_school=None, force_requires_signature: Optional[bool] = None):
        from .tasks import send_notification_task
        from django.db import transaction

        cleaned = self.cleaned_data

        # تحديد المدرسة المرتبطة بالإشعار
        school_for_notification = default_school
        try:
            from .permissions import is_platform_admin
            is_superuser = bool(getattr(creator, "is_superuser", False))
            is_platform = bool(is_platform_admin(creator)) and not is_superuser
        except Exception:
            is_superuser = bool(getattr(creator, "is_superuser", False))
            is_platform = False

        if is_superuser or is_platform:
            scope = cleaned.get("audience_scope") or "school"
            # للتعاميم: لو لم تُحدد مدرسة بعينها (خصوصاً للمشرف العام)، نعاملها كنطاق "all".
            if is_platform and scope == "school" and not cleaned.get("target_school"):
                scope = "all"
            if scope == "all":
                school_for_notification = None
            else:
                school_for_notification = cleaned.get("target_school") or None

        requires_signature = bool(cleaned.get("requires_signature"))
        if force_requires_signature is not None:
            requires_signature = bool(force_requires_signature)

        # المرفقات للتعاميم فقط
        attachment = None
        if requires_signature:
            attachment = cleaned.get("attachment") if "attachment" in cleaned else None

        n = Notification.objects.create(
            title=cleaned.get("title") or "",
            message=cleaned["message"],
            is_important=bool(cleaned.get("is_important")),
            expires_at=cleaned.get("expires_at") or None,
            attachment=attachment,
            requires_signature=requires_signature,
            signature_deadline_at=(cleaned.get("signature_deadline_at") or None) if requires_signature else None,
            signature_ack_text=(cleaned.get("signature_ack_text") or "").strip()
            or "أقرّ بأنني اطلعت على هذا التعميم وفهمت ما ورد فيه وأتعهد بالالتزام به.",
            created_by=creator,
            school=school_for_notification,
        )
        
        # تجميع المستهدفين
        teacher_ids_set = set()
        
        # 1. المعلمون المختارون يدوياً
        selected_teachers = cleaned.get("teachers")
        if selected_teachers:
            teacher_ids_set.update([t.pk for t in selected_teachers])
            
        # 2. توجيه حسب القسم (للإشعارات فقط)
        target_dept = cleaned.get("target_department")
        # ملاحظة: اختيار القسم في الواجهة غالباً يُستخدم لتصفية القائمة.
        # لذلك لا نرسل للقسم بالكامل إذا اختار المستخدم معلمين بشكل يدوي.
        if target_dept and not bool(requires_signature) and not selected_teachers:
            from .models import DepartmentMembership
            dept_teachers = DepartmentMembership.objects.filter(
                department=target_dept,
                teacher__is_active=True,
            )
            if school_for_notification is not None:
                dept_teachers = dept_teachers.filter(
                    teacher__school_memberships__school=school_for_notification,
                    teacher__school_memberships__is_active=True,
                )
            dept_teachers = dept_teachers.values_list("teacher_id", flat=True).distinct()
            teacher_ids_set.update(dept_teachers)
        
        teacher_ids = list(teacher_ids_set) if teacher_ids_set else None

        # التعميمات (requires_signature=True): مدير النظام/المشرف العام يرسل لمدراء المدارس
        # لو لم يحدد أسماء أو فعّل "إرسال للكل"، نعتبره "إرسال للكل" ضمن النطاق المحدد.
        try:
            from .permissions import is_platform_admin
            is_platform_creator = bool(is_platform_admin(creator)) and not bool(getattr(creator, "is_superuser", False))
        except Exception:
            is_platform_creator = False

        send_to_all_managers = cleaned.get("send_to_all_managers", False)
        if bool(requires_signature) and (bool(getattr(creator, "is_superuser", False)) or is_platform_creator):
            if send_to_all_managers or not teacher_ids:
                try:
                    qs = self.fields["teachers"].queryset
                    teacher_ids = list(qs.values_list("pk", flat=True))
                except Exception:
                    teacher_ids = None

        # Circulars inside a school require explicit recipients selection.
        # Keep teacher_ids as-is; do not expand an empty selection to all teachers.

        # Reliability guard: when recipients are explicitly known, create the
        # DB recipient rows immediately.  Celery may still run later for
        # realtime pushes, but page delivery no longer depends on a live worker.
        if teacher_ids:
            try:
                NotificationRecipient.objects.bulk_create(
                    [NotificationRecipient(notification=n, teacher_id=tid) for tid in teacher_ids],
                    ignore_conflicts=True,
                )
                try:
                    from .cache_utils import invalidate_user_notifications

                    for tid in teacher_ids:
                        invalidate_user_notifications(int(tid))
                except Exception:
                    pass
            except Exception:
                logger.exception("Immediate notification recipient creation failed for notification %s", n.pk)

        # Trigger background task to create recipients
        # - Prefer async (Celery)
        # - Fallback to local execution if broker/worker is unavailable
        def _dispatch():
            import time

            try:
                from django.conf import settings
            except Exception:
                settings = None  # type: ignore

            broker_url = ""
            try:
                broker_url = (getattr(settings, "CELERY_BROKER_URL", "") or "").strip()
            except Exception:
                broker_url = ""

            # Best-effort anti-double-send across async/local paths.
            try:
                from django.core.cache import cache
                lock_ttl = int(getattr(settings, "NOTIFICATIONS_DISPATCH_LOCK_TTL_SECONDS", 900))
            except Exception:
                cache = None  # type: ignore
                lock_ttl = 900

            # Versioned lock key to keep uniqueness stable across future changes.
            lock_key = f"notif_dispatch_lock:v1:notification:{n.pk}"

            def _acquire_lock() -> bool:
                if cache is None:
                    return True
                try:
                    return bool(cache.add(lock_key, "1", timeout=max(60, int(lock_ttl))))
                except Exception:
                    return True

            def _release_lock() -> None:
                if cache is None:
                    return
                try:
                    cache.delete(lock_key)
                except Exception:
                    pass

            def _run_local(*, warn_seconds: float, is_debug: bool) -> bool:
                started = time.monotonic()

                try:
                    from django.db import close_old_connections
                    close_old_connections()
                except Exception:
                    pass

                ok = False
                try:
                    send_notification_task.apply(args=(n.pk, teacher_ids), throw=True)
                    ok = True
                except Exception as exc:
                    if is_debug:
                        logger.exception("Local notification dispatch failed")
                    else:
                        logger.error("Local notification dispatch failed: %s", exc)
                finally:
                    dur = time.monotonic() - started
                    if dur > float(warn_seconds or 0):
                        logger.warning("Local notification dispatch took %.2fs (notification=%s)", dur, n.pk)

                return ok

            recipient_count = None if teacher_ids is None else len(teacher_ids)

            # ------------------ No broker configured: local fallback path ------------------
            if not broker_url:
                try:
                    if not bool(getattr(settings, "NOTIFICATIONS_LOCAL_FALLBACK_ENABLED", True)):
                        logger.error(
                            "Celery broker not configured and local fallback disabled; notification %s will not be dispatched",
                            n.pk,
                        )
                        return

                    use_thread = bool(getattr(settings, "NOTIFICATIONS_LOCAL_FALLBACK_THREAD", True))
                    max_sync = int(getattr(settings, "NOTIFICATIONS_LOCAL_FALLBACK_MAX_RECIPIENTS", 500))
                    hard_stop = int(getattr(settings, "NOTIFICATIONS_LOCAL_FALLBACK_HARD_STOP_RECIPIENTS", 500))
                    warn_seconds = float(getattr(settings, "NOTIFICATIONS_LOCAL_FALLBACK_WARN_SECONDS", 2.5))
                    is_debug = bool(getattr(settings, "DEBUG", False))
                except Exception:
                    use_thread = True
                    max_sync = 500
                    hard_stop = 500
                    warn_seconds = 2.5
                    is_debug = False

                # Hard-stop: do not run heavy/unknown workloads inside web.
                # When teacher_ids is None it means "all school teachers" – resolve the count
                # from DB so we can apply the hard_stop guard without silently dropping the send.
                if recipient_count is None:
                    try:
                        from .models import SchoolMembership
                        _school = getattr(n, "school", None)
                        if _school is not None:
                            recipient_count = (
                                SchoolMembership.objects.filter(
                                    school=_school,
                                    is_active=True,
                                    role_type=SchoolMembership.RoleType.TEACHER,
                                )
                                .values("teacher_id")
                                .distinct()
                                .count()
                            )
                        else:
                            recipient_count = 0
                    except Exception:
                        recipient_count = 0

                if int(recipient_count) > int(hard_stop):
                    logger.error(
                        "Local notification fallback refused (recipients=%s, hard_stop=%s). Configure broker to dispatch notification %s.",
                        recipient_count,
                        hard_stop,
                        n.pk,
                    )
                    return

                if not _acquire_lock():
                    logger.info("Notification %s dispatch already in progress; skipping", n.pk)
                    return

                global _NOTIF_CELERY_FALLBACK_WARNED
                if not _NOTIF_CELERY_FALLBACK_WARNED:
                    logger.warning("Celery broker not configured; using local fallback for notifications")
                    _NOTIF_CELERY_FALLBACK_WARNED = True

                should_thread = bool(use_thread) and int(recipient_count) > int(max_sync)
                if should_thread:
                    try:
                        import threading

                        threading.Thread(
                            target=lambda: (_run_local(warn_seconds=warn_seconds, is_debug=is_debug) or _release_lock()),
                            name="notif_local_dispatch",
                            daemon=True,
                        ).start()
                        return
                    except Exception:
                        pass

                ok = False
                try:
                    ok = _run_local(warn_seconds=warn_seconds, is_debug=is_debug)
                finally:
                    if not ok:
                        _release_lock()
                return

            # ------------------ Broker configured: async path ------------------
            if not _acquire_lock():
                logger.info("Notification %s dispatch already in progress; skipping", n.pk)
                return

            enqueued = False
            try:
                try:
                    from core.trace_context import get_trace_id as _get_trace_id
                    _tid = _get_trace_id()
                except Exception:
                    _tid = None
                if not _tid:
                    import secrets
                    _tid = secrets.token_hex(8)
                send_notification_task.apply_async(
                    args=[n.pk, teacher_ids],
                    headers={"trace_id": _tid},
                )
                enqueued = True
                return
            except Exception:
                logger.exception("Celery enqueue failed; attempting local fallback")
            finally:
                # If enqueue failed, release the lock so fallback (or later retry) can proceed.
                if not enqueued:
                    _release_lock()

            # If enqueue failed (broker down), local fallback may be allowed for small/known workloads.
            try:
                if not bool(getattr(settings, "NOTIFICATIONS_LOCAL_FALLBACK_ENABLED", True)):
                    logger.error("Celery enqueue failed and local fallback disabled; notification %s not dispatched", n.pk)
                    return

                use_thread = bool(getattr(settings, "NOTIFICATIONS_LOCAL_FALLBACK_THREAD", True))
                max_sync = int(getattr(settings, "NOTIFICATIONS_LOCAL_FALLBACK_MAX_RECIPIENTS", 500))
                hard_stop = int(getattr(settings, "NOTIFICATIONS_LOCAL_FALLBACK_HARD_STOP_RECIPIENTS", 500))
                warn_seconds = float(getattr(settings, "NOTIFICATIONS_LOCAL_FALLBACK_WARN_SECONDS", 2.5))
                is_debug = bool(getattr(settings, "DEBUG", False))
            except Exception:
                use_thread = True
                max_sync = 500
                hard_stop = 500
                warn_seconds = 2.5
                is_debug = False

            # Resolve recipient_count if still unknown (teacher_ids was None).
            if recipient_count is None:
                try:
                    from .models import SchoolMembership
                    _school = getattr(n, "school", None)
                    if _school is not None:
                        recipient_count = (
                            SchoolMembership.objects.filter(
                                school=_school,
                                is_active=True,
                                role_type=SchoolMembership.RoleType.TEACHER,
                            )
                            .values("teacher_id")
                            .distinct()
                            .count()
                        )
                    else:
                        recipient_count = 0
                except Exception:
                    recipient_count = 0

            if int(recipient_count) > int(hard_stop):
                logger.error(
                    "Celery enqueue failed; local fallback refused (recipients=%s, hard_stop=%s) for notification %s",
                    recipient_count,
                    hard_stop,
                    n.pk,
                )
                return

            # Acquire lock for local attempt (avoid duplicates if concurrent retries).
            if not _acquire_lock():
                logger.info("Notification %s dispatch already in progress; skipping", n.pk)
                return

            should_thread = bool(use_thread) and int(recipient_count) > int(max_sync)
            if should_thread:
                try:
                    import threading

                    threading.Thread(
                        target=lambda: (_run_local(warn_seconds=warn_seconds, is_debug=is_debug) or _release_lock()),
                        name="notif_local_dispatch",
                        daemon=True,
                    ).start()
                    return
                except Exception:
                    pass

            ok = False
            try:
                ok = _run_local(warn_seconds=warn_seconds, is_debug=is_debug)
            finally:
                if not ok:
                    _release_lock()

        transaction.on_commit(_dispatch)
        
        return n


class SupportTicketForm(forms.ModelForm):
    """نموذج إنشاء تذكرة دعم فني للمنصة."""

    # نستخدم ImageField هنا لضمان التحقق من أنه صورة قبل الحفظ،
    # وللسماح بضغط الصورة قبل التحقق من حد الحجم النهائي.
    attachment = forms.ImageField(
        required=False,
        widget=forms.ClearableFileInput(attrs={
            "class": "form-control",
            "accept": "image/*",
        }),
    )

    class Meta:
        model = Ticket
        fields = ["title", "body", "attachment"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "عنوان المشكلة أو الاستفسار", "maxlength": "255"
            }),
            "body": forms.Textarea(attrs={"class": "form-control", "rows": 5, "placeholder": "اشرح المشكلة بالتفصيل..."}),
        }

    def clean_attachment(self):
        f = self.cleaned_data.get("attachment")
        if not f:
            return f

        # ضغط/تصغير قبل الرفع
        try:
            from PIL import Image, ImageOps, UnidentifiedImageError

            img = Image.open(f)
            img = ImageOps.exif_transpose(img)
        except (UnidentifiedImageError, OSError, ValueError):
            raise ValidationError("الملف المرفق ليس صورة صالحة.")

        has_alpha = img.mode in ("RGBA", "LA", "P")
        img = img.convert("RGBA" if has_alpha else "RGB")

        max_px = 1600
        if max(img.size) > max_px:
            img.thumbnail((max_px, max_px), Image.LANCZOS)

        buf = BytesIO()
        base = os.path.splitext(getattr(f, "name", "image"))[0]

        if has_alpha:
            # PNG للصور ذات الشفافية
            img.save(buf, format="PNG", optimize=True, compress_level=9)
            new_name = f"{base}.png"
            ctype = "image/png"
        else:
            # JPEG للصور العادية (ضغط أعلى)
            img.save(buf, format="JPEG", quality=82, optimize=True, progressive=True)
            new_name = f"{base}.jpg"
            ctype = "image/jpeg"

        buf.seek(0)
        out = InMemoryUploadedFile(
            buf,
            getattr(f, "field_name", None) or "attachment",
            new_name,
            ctype,
            buf.getbuffer().nbytes,
            None,
        )

        # حد الحجم بعد الضغط (مطابق للحد في الموديل: 5MB)
        max_bytes = 5 * 1024 * 1024
        if out.size > max_bytes:
            raise ValidationError("حجم الصورة بعد الضغط ما يزال كبيرًا (الحد الأقصى 5MB).")

        return out

    def save(self, commit=True, user=None):
        ticket = super().save(commit=False)
        if user:
            ticket.creator = user
        ticket.is_platform = True
        if commit:
            ticket.save()
        return ticket


# ==============================
# نماذج الاشتراكات (Platform Admin)
# ==============================
class SubscriptionPlanForm(forms.ModelForm):
    class Meta:
        model = SubscriptionPlan
        fields = ["name", "description", "price", "days_duration", "max_teachers", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "اسم الخطة (مثلاً: باقة سنوية)"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "وصف مميزات الخطة..."}),
            "price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "days_duration": forms.NumberInput(attrs={"class": "form-control"}),
            "max_teachers": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "name": "اسم الخطة",
            "description": "الوصف",
            "price": "السعر (ريال)",
            "days_duration": "المدة (بالأيام)",
            "max_teachers": "حد المعلمين",
            "is_active": "نشط؟",
        }


class SchoolSubscriptionForm(forms.ModelForm):
    """نموذج اشتراك المدرسة (للوحة المنصة).

    المطلوب: حساب التواريخ تلقائياً حسب مدة الباقة (days_duration) اعتماداً على التاريخ الميلادي.
    - start_date = اليوم
    - end_date = اليوم + (days_duration - 1)
    """

    class Meta:
        model = SchoolSubscription
        fields = ["school", "plan", "is_active"]
        widgets = {
            "school": forms.Select(attrs={"class": "form-select"}),
            "plan": forms.Select(attrs={"class": "form-select"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "school": "المدرسة",
            "plan": "الباقة",
            "is_active": "نشط؟",
        }

    def __init__(self, *args, **kwargs):
        self._allow_plan_change = bool(kwargs.pop("allow_plan_change", False))
        super().__init__(*args, **kwargs)
        # ✅ عند تعديل اشتراك موجود: لا نسمح بتغيير المدرسة.
        # ✅ الباقة: افتراضياً لا نسمح بتغييرها، لكن يمكن السماح بذلك في حالات
        # تجديد اشتراك مُلغى/منتهي من لوحة المنصة.
        try:
            if getattr(self.instance, "pk", None):
                if "school" in self.fields:
                    self.fields["school"].disabled = True
                if (not self._allow_plan_change) and "plan" in self.fields:
                    self.fields["plan"].disabled = True
        except Exception:
            pass

    def clean_school(self):
        # تحصين: حتى مع التلاعب بالـ POST لا نسمح بتغيير المدرسة للاشتراك الموجود.
        if getattr(self.instance, "pk", None):
            return self.instance.school
        return self.cleaned_data.get("school")

    def clean_plan(self):
        # تحصين: حتى مع التلاعب بالـ POST لا نسمح بتغيير الباقة للاشتراك الموجود.
        if getattr(self.instance, "pk", None) and (not self._allow_plan_change):
            return self.instance.plan
        return self.cleaned_data.get("plan")

    def save(self, commit=True):
        from datetime import timedelta

        subscription: SchoolSubscription = super().save(commit=False)
        plan = self.cleaned_data.get("plan")

        # ✅ عند الإنشاء فقط: احسب التواريخ تلقائياً.
        # عند التعديل: لا نغير التواريخ (التجديد له زر/مسار مستقل).
        if getattr(subscription, "pk", None) is None:
            today = timezone.localdate()
            subscription.start_date = today

            days = int(getattr(plan, "days_duration", 0) or 0)
            if days <= 0:
                subscription.end_date = today
            else:
                # end_date = اليوم + (المدة - 1) حتى تكون الأيام الفعلية = days_duration
                subscription.end_date = today + timedelta(days=days - 1)

        if commit:
            subscription.save()
        return subscription


# ==============================
# 📁 ملف إنجاز المعلّم (سنوي)
# ==============================
class AchievementCreateYearForm(forms.Form):
    """اختيار سنة دراسية من قائمة لتفادي أخطاء الكتابة."""

    BASE_HIJRI_YEARS: List[str] = [
        "1447-1448",
        "1448-1449",
        "1449-1450",
    ]

    academic_year = forms.ChoiceField(
        label="السنة الدراسية (هجري)",
        choices=[],
        widget=forms.Select(attrs={"class": "input"}),
        help_text="اختر السنة من القائمة.",
    )

    def __init__(
        self,
        *args,
        year_choices: Optional[List[str]] = None,
        allowed_years: Optional[List[str]] = None,
        **kwargs,
    ):
        super().__init__(*args, **kwargs)

        def _norm(v: str) -> str:
            return (v or "").strip().replace("–", "-").replace("—", "-")

        def _parse(v: str) -> Optional[Tuple[int, int]]:
            import re

            vv = _norm(v)
            if not re.fullmatch(r"\d{4}-\d{4}", vv):
                return None
            s, e = vv.split("-", 1)
            try:
                si, ei = int(s), int(e)
            except Exception:
                return None
            if ei != si + 1:
                return None
            return si, ei

        existing = [_norm(y) for y in (year_choices or []) if (y or "").strip()]

        # إذا تم تمرير سنوات مسموحة (من إعدادات المدرسة) نستخدمها فقط
        # وإلا نستخدم القائمة الافتراضية
        if allowed_years and len(allowed_years) > 0:
            base_set = set([_norm(y) for y in allowed_years])
            # لا نقوم بتوليد سنوات مستقبلية تلقائيًا إذا حدد المدير القائمة
            all_years = base_set.union(existing)
        else:
            all_years = set([_norm(y) for y in self.BASE_HIJRI_YEARS] + existing)
            # توليد سنوات مستقبلية تلقائيًا في الحالة الافتراضية
            parsed = [_parse(y) for y in all_years]
            parsed_ok = [p for p in parsed if p is not None]
            max_end = max([e for _, e in parsed_ok], default=1450)
            for i in range(0, 2):
                s = max_end + i
                all_years.add(f"{s}-{s + 1}")

        valid = sorted(
            [y for y in all_years if _parse(y) is not None],
            key=lambda v: int(v.split("-", 1)[0]),
            reverse=False,
        )
        choices = [(y, f"{y} هـ") for y in valid]
        self.fields["academic_year"].choices = choices
        if choices:
            is_in_choices = False
            if self.initial.get("academic_year"):
                 # Check if initial is in choices
                 if any(c[0] == self.initial["academic_year"] for c in choices):
                     is_in_choices = True
            
            if not is_in_choices: 
                 self.fields["academic_year"].initial = choices[0][0]

    def clean_academic_year(self):
        return _validate_academic_year_hijri(self.cleaned_data.get("academic_year", ""))


class TeacherAchievementFileForm(forms.ModelForm):
    class Meta:
        model = TeacherAchievementFile
        fields = [
            "qualifications",
            "professional_experience",
            "specialization",
            "teaching_load",
            "subjects_taught",
            "contact_info",
        ]
        widgets = {
            "qualifications": forms.Textarea(attrs={"class": "textarea", "rows": 4}),
            "professional_experience": forms.Textarea(attrs={"class": "textarea", "rows": 4}),
            "specialization": forms.Textarea(attrs={"class": "textarea", "rows": 3}),
            "teaching_load": forms.Textarea(attrs={"class": "textarea", "rows": 2}),
            "subjects_taught": forms.Textarea(attrs={"class": "textarea", "rows": 3}),
            "contact_info": forms.Textarea(attrs={"class": "textarea", "rows": 3}),
        }


class AchievementSectionNotesForm(forms.ModelForm):
    class Meta:
        model = AchievementSection
        fields = ["teacher_notes"]
        widgets = {"teacher_notes": forms.Textarea(attrs={"class": "textarea", "rows": 3})}


class _AchievementMultiImageInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class AchievementEvidenceUploadForm(forms.Form):
    images = forms.FileField(
        label="إضافة صور الشواهد",
        required=False,
        widget=_AchievementMultiImageInput(attrs={"multiple": True, "class": "input", "accept": "image/*"}),
        help_text="حد أقصى 8 صور لكل محور.",
    )


class AchievementManagerNotesForm(forms.ModelForm):
    class Meta:
        model = TeacherAchievementFile
        fields = ["manager_notes"]
        widgets = {
            "manager_notes": forms.Textarea(
                attrs={
                    "class": "textarea",
                    "rows": 4,
                    "placeholder": "اكتب شكرًا/تحفيزًا عند الاعتماد، أو سبب الرفض عند الإرجاع…",
                }
            )
        }
