# reports/forms.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional, List, Tuple
from io import BytesIO
import os
import logging

from django import forms
from django.contrib.auth.forms import PasswordChangeForm
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import models, transaction
from django.db.models import Q
from django.utils.text import slugify
from django.utils import timezone

# ==============================
# استيراد الموديلات (من models.py فقط)
# ==============================
from .models import (
    Teacher,
    Role,
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

logger = logging.getLogger(__name__)

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
        for name, f in self.fields.items():
            try:
                f.widget.attrs.setdefault("class", "form-control")
                if name == "old_password":
                    f.widget.attrs.setdefault("autocomplete", "current-password")
                else:
                    f.widget.attrs.setdefault("autocomplete", "new-password")
            except Exception:
                pass


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

        target_role = None
        if dep:
            if dep.slug in TEACHERS_DEPT_SLUGS:
                target_role = Role.objects.filter(slug="teacher").first()
            else:
                target_role = Role.objects.filter(slug=dep.slug).first()
        instance.role = target_role  # قد تكون None

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

        # توافق تراثي: Teacher.role ليس مصدر الصلاحيات (SchoolMembership/DepartmentMembership)
        # لكن نضبطه على teacher إن كان موجودًا.
        try:
            instance.role = Role.objects.filter(slug="teacher").first()
        except Exception:
            instance.role = None

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

        # توافق تراثي: نجعل role=teacher.
        try:
            instance.role = Role.objects.filter(slug="teacher").first()
        except Exception:
            instance.role = None

        if commit:
            instance.save()
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
        try:
            instance.role = Role.objects.filter(slug="teacher").first()
        except Exception:
            instance.role = None
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

# ==============================
# 📌 إنشاء إشعار
# ==============================
class NotificationCreateForm(forms.Form):
    title = forms.CharField(max_length=120, required=False, label="عنوان (اختياري)")
    message = forms.CharField(widget=forms.Textarea(attrs={"rows":5}), label="نص الإشعار")
    is_important = forms.BooleanField(required=False, initial=False, label="مهم")
    expires_at = forms.DateTimeField(required=False, label="ينتهي في (اختياري)",
                                     widget=forms.DateTimeInput(attrs={"type":"datetime-local"}))

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
        help_text="اختر قسماً لإرسال الإشعار لجميع منسوبيه (رئيس القسم والمكلفين).",
    )
    teachers = forms.ModelMultipleChoiceField(
        queryset=Teacher.objects.none(),
        required=False,
        label="المستلمون (يمكن اختيار أكثر من معلم)",
        widget=forms.CheckboxSelectMultiple()
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        active_school = kwargs.pop("active_school", None)
        mode = (kwargs.pop("mode", None) or "notification").strip().lower()
        super().__init__(*args, **kwargs)

        self.user = user
        self.mode = mode if mode in {"notification", "circular"} else "notification"
        is_circular = self.mode == "circular"

        is_superuser = bool(getattr(user, "is_superuser", False))
        
        # التحقق مما إذا كان المستخدم مديراً ضمن المدرسة النشطة (عزل مدارس)
        try:
            from .views import _is_manager_in_school
            is_manager = bool(_is_manager_in_school(user, active_school))
        except Exception:
            is_manager = False

        # إعداد حقول نطاق الإرسال/المدرسة حسب نوع المستخدم
        if is_superuser:
            self.fields["target_school"].queryset = School.objects.filter(is_active=True).order_by("name")
            # السوبر يوزر يمكنه اختيار أي قسم في المنصة
            self.fields["target_department"].queryset = Department.objects.filter(is_active=True).order_by("name")
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
                    self.fields["teachers"].label = "المعلمون (يمكن اختيار معلم أو أكثر)"
                    self.fields["teachers"].help_text = "يمكنك ترك الاختيار فارغًا لإرسال التعميم لجميع المعلمين في المدرسة."

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
        if getattr(user, "is_superuser", False):
            scope = cleaned.get("audience_scope") or "school"
            target_school = cleaned.get("target_school")
            if scope == "school" and not target_school:
                raise ValidationError("الرجاء اختيار مدرسة مستهدفة أو تغيير النطاق إلى \"كل المدارس\".")
        return cleaned

    def save(self, creator, default_school=None, force_requires_signature: Optional[bool] = None):
        from .tasks import send_notification_task
        from django.db import transaction

        cleaned = self.cleaned_data

        # تحديد المدرسة المرتبطة بالإشعار
        school_for_notification = default_school
        if getattr(creator, "is_superuser", False):
            scope = cleaned.get("audience_scope") or "school"
            if scope == "all":
                school_for_notification = None
            else:
                school_for_notification = cleaned.get("target_school") or None

        requires_signature = bool(cleaned.get("requires_signature"))
        if force_requires_signature is not None:
            requires_signature = bool(force_requires_signature)

        n = Notification.objects.create(
            title=cleaned.get("title") or "",
            message=cleaned["message"],
            is_important=bool(cleaned.get("is_important")),
            expires_at=cleaned.get("expires_at") or None,
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
        if target_dept and not bool(requires_signature):
            from .models import DepartmentMembership
            dept_teachers = DepartmentMembership.objects.filter(department=target_dept).values_list("teacher_id", flat=True)
            teacher_ids_set.update(dept_teachers)
        
        teacher_ids = list(teacher_ids_set) if teacher_ids_set else None

        # التعميمات (requires_signature=True): مدير النظام يرسل لمدراء المدارس
        # لو لم يحدد أسماء، نعتبره "إرسال للكل" ضمن النطاق المحدد.
        if bool(requires_signature) and bool(getattr(creator, "is_superuser", False)) and not teacher_ids:
            try:
                qs = self.fields["teachers"].queryset
                teacher_ids = list(qs.values_list("pk", flat=True))
            except Exception:
                teacher_ids = None

        # Trigger background task to create recipients
        # - Prefer async (Celery)
        # - Fallback to local execution if broker/worker is unavailable
        def _dispatch():
            try:
                send_notification_task.delay(n.pk, teacher_ids)
            except Exception:
                logger.exception("Celery enqueue failed; running send_notification_task locally")
                # Run in-process (does not require broker)
                send_notification_task.apply(args=(n.pk, teacher_ids), throw=True)

        transaction.on_commit(_dispatch)
        
        return n


class SupportTicketForm(forms.ModelForm):
    """
    نموذج إنشاء تذكرة دعم فني للمنصة.
    """

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

    def save(self, commit=True):
        from datetime import timedelta

        subscription: SchoolSubscription = super().save(commit=False)
        plan = self.cleaned_data.get("plan")
        today = timezone.now().date()

        # تجديد/إنشاء: نبدأ من اليوم دائماً
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

    def __init__(self, *args, year_choices: Optional[List[str]] = None, **kwargs):
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
        all_years = set([_norm(y) for y in self.BASE_HIJRI_YEARS] + existing)

        # توليد سنوات مستقبلية بدون إدخال يدوي (مثلاً: 1450-1451)
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
