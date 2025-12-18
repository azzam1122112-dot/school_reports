# reports/forms.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Optional, List, Tuple
from io import BytesIO
import os

from django import forms
from django.core.exceptions import ValidationError
from django.core.validators import RegexValidator
from django.core.files.uploadedfile import InMemoryUploadedFile
from django.db import models, transaction
from django.db.models import Q
from django.utils.text import slugify

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
)

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

# ==============================
# مساعدات داخلية للأقسام/المستخدمين
# ==============================
def _teachers_for_dept(dept_slug: str, school: Optional["School"] = None):
    """
    إرجاع QuerySet للمعلمين المنتمين لقسم معيّن.
    - عبر عضوية DepartmentMembership (department ←→ teacher)

    ملاحظة: لا نعتمد على Role.slug لأن الأقسام أصبحت مخصصة لكل مدرسة ويمكن تكرار slugs.
    """
    if not dept_slug:
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
            school_memberships__is_active=True,
        )

    teacher_ids = DepartmentMembership.objects.filter(department=dep).values_list("teacher_id", flat=True)
    return base_qs.filter(id__in=teacher_ids).only("id", "name").order_by("name").distinct()


def _is_teacher_in_dept(teacher: Teacher, dept_slug: str) -> bool:
    """هل المعلّم ينتمي للقسم؟"""
    if not teacher or not dept_slug:
        return False

    dept_slug_norm = (dept_slug or "").strip().lower()
    dep = Department.objects.filter(slug__iexact=dept_slug_norm).first()
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

        # ضغط الصور قبل الرفع إلى Cloudinary + التحقق من الحجم بعد الضغط
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
        queryset=Department.objects.filter(is_active=True).order_by("name"),
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
            if active_school is not None and hasattr(Department, "school"):
                dept_qs = dept_qs.filter(school=active_school)
            self.fields["department"].queryset = dept_qs.order_by("name")
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

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # في وضع التعديل تكون كلمة المرور اختيارية، وتُستخدم فقط عند إدخال قيمة جديدة
        if self.instance and getattr(self.instance, "pk", None):
            self.fields["password"].required = False
            self.fields["password"].widget.attrs.setdefault(
                "placeholder", "اتركه فارغًا للإبقاء على كلمة المرور الحالية"
            )

    def clean_national_id(self):
        nid = (self.cleaned_data.get("national_id") or "").strip()
        if nid:
            if not nid.isdigit() or len(nid) != 10:
                raise ValidationError("رقم الهوية يجب أن يتكون من 10 أرقام.")
        return nid or None

    def save(self, commit: bool = True):
        instance: Teacher = super().save(commit=False)
        new_pwd = (self.cleaned_data.get("password") or "").strip()
        # إنشاء: إن لم تُحدّد كلمة مرور نضبط كلمة مرور غير قابلة للاستخدام.
        # تعديل: إن تُرك الحقل فارغًا نحافظ على كلمة المرور الحالية.
        if new_pwd:
            instance.set_password(new_pwd)
        elif not getattr(instance, "pk", None):
            instance.set_unusable_password()
        if commit:
            instance.save()
        return instance

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
    - assignee يُبنى ديناميكيًا
    - images اختيارية ومتعددة (MultiFileField)
    """

    department = forms.ModelChoiceField(
        label="القسم",
        queryset=Department.objects.filter(is_active=True).order_by("name"),
        required=True,
        empty_label="— اختر القسم —",
        to_field_name="slug",
        widget=forms.Select(attrs={"class": "form-select", "id": "id_department"}),
    )

    assignee = forms.ModelChoiceField(
        label="المستلم",
        queryset=Teacher.objects.none(),
        required=False,
        widget=forms.Select(attrs={"class": "form-select", "id": "id_assignee"}),
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
        fields = ["department", "assignee", "title", "body"]
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
            if active_school is not None and hasattr(Department, "school"):
                dept_qs = dept_qs.filter(school=active_school)
            self.fields["department"].queryset = dept_qs.order_by("name")

        # تأكيد اختياريّة الصور (تحصين إضافي)
        self.fields["images"].required = False

        # بناء قائمة المستلمين حسب القسم
        dept_value = (self.data.get("department") or "").strip() if self.is_bound \
            else getattr(getattr(self.instance, "department", None), "slug", "") or ""
        base_qs = _teachers_for_dept(dept_value, active_school) if dept_value else Teacher.objects.none()
        self.fields["assignee"].queryset = base_qs

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
        assignee: Optional[Teacher] = cleaned.get("assignee")

        if not dept:
            self.add_error("department", "الرجاء اختيار القسم.")
        if dept and not assignee and self.fields["assignee"].queryset.count() > 1:
            self.add_error("assignee", "يرجى اختيار الموظّف.")
        if assignee and dept:
            if self.active_school is not None:
                is_allowed = _teachers_for_dept(dept.slug, self.active_school).filter(id=assignee.id).exists()
            else:
                is_allowed = _is_teacher_in_department(assignee, dept)
            if not is_allowed:
                self.add_error("assignee", "الموظّف المختار لا ينتمي إلى هذا القسم.")

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

        if user is not None and not obj.pk:
            obj.creator = user
        if not getattr(obj, "status", None):
            try:
                obj.status = Ticket.Status.OPEN  # type: ignore[attr-defined]
            except Exception:
                pass

        if commit:
            obj.save()
            if self._compressed_images:
                from .models import TicketImage
                for f in self._compressed_images:
                    TicketImage.objects.create(ticket=obj, image=f)
        return obj

    # -----------------------------
    # الحفظ وإنشاء سجلات الصور
    # -----------------------------
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

        if commit:
            obj.save()
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
        fields = ["name", "slug", "role_label", "is_active", "reporttypes"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "maxlength": "120"}),
            "slug": forms.TextInput(attrs={"class": "form-control", "maxlength": "64"}),
            "role_label": forms.TextInput(attrs={"class": "form-control", "maxlength": "120"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }

    def clean_slug(self):
        slug = (self.cleaned_data.get("slug") or "").strip().lower()
        if not slug:
            slug = slugify(self.cleaned_data.get("name") or "", allow_unicode=True)
        qs = Department.objects.filter(slug=slug)
        if self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        if qs.exists():
            raise forms.ValidationError("المعرّف (slug) مستخدم مسبقًا لقسم آخر.")
        return slug

    def __init__(self, *args, **kwargs):
        active_school = kwargs.pop("active_school", None)
        super().__init__(*args, **kwargs)

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
    teachers = forms.ModelMultipleChoiceField(
        queryset=Teacher.objects.none(),
        required=True,
        label="المستلمون (يمكن اختيار أكثر من معلم)",
        widget=forms.CheckboxSelectMultiple()
    )

    def __init__(self, *args, **kwargs):
        user = kwargs.pop("user", None)
        active_school = kwargs.pop("active_school", None)
        super().__init__(*args, **kwargs)

        self.user = user

        is_superuser = bool(getattr(user, "is_superuser", False))

        # إعداد حقول نطاق الإرسال/المدرسة حسب نوع المستخدم
        if is_superuser:
            self.fields["target_school"].queryset = School.objects.filter(is_active=True).order_by("name")
        else:
            # لا يحتاج المدير/الضابط لاختيار النطاق أو المدرسة؛ نستخدم المدرسة النشطة تلقائياً
            self.fields.pop("audience_scope", None)
            self.fields.pop("target_school", None)

        qs = Teacher.objects.filter(is_active=True).order_by("name")

        # تقليص القائمة حسب الأقسام التي يديرها المستخدم (للضباط)
        try:
            role_slug = getattr(getattr(user, "role", None), "slug", None)
            if role_slug and role_slug not in (None, "manager"):
                from .views import _user_department_codes  # تفادِ الاستيراد في أعلى الملف
                codes = _user_department_codes(user)
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
                school_memberships__is_active=True,
            ).distinct()

        # للمشرف العام: لو اختار "مدرسة معيّنة" في الطلب، نقيّد القائمة بهذه المدرسة
        if is_superuser:
            scope_val = (self.data.get("audience_scope") or self.initial.get("audience_scope") or "").strip()
            school_id = self.data.get("target_school") or self.initial.get("target_school")
            if (not scope_val or scope_val == "school") and school_id:
                try:
                    qs = qs.filter(
                        school_memberships__school_id=int(school_id),
                        school_memberships__is_active=True,
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

    def save(self, creator, default_school=None):
        cleaned = self.cleaned_data

        # تحديد المدرسة المرتبطة بالإشعار
        school_for_notification = default_school
        if getattr(creator, "is_superuser", False):
            scope = cleaned.get("audience_scope") or "school"
            if scope == "all":
                school_for_notification = None
            else:
                school_for_notification = cleaned.get("target_school") or None

        n = Notification.objects.create(
            title=cleaned.get("title") or "",
            message=cleaned["message"],
            is_important=bool(cleaned.get("is_important")),
            expires_at=cleaned.get("expires_at") or None,
            created_by=creator,
            school=school_for_notification,
        )
        teachers = list(cleaned["teachers"])
        if teachers:
            NotificationRecipient.objects.bulk_create([
                NotificationRecipient(notification=n, teacher=t) for t in teachers
            ], ignore_conflicts=True)
        return n


class SupportTicketForm(forms.ModelForm):
    """
    نموذج إنشاء تذكرة دعم فني للمنصة.
    """
    class Meta:
        model = Ticket
        fields = ["title", "body", "attachment"]
        widgets = {
            "title": forms.TextInput(attrs={
                "class": "form-control", "placeholder": "عنوان المشكلة أو الاستفسار", "maxlength": "255"
            }),
            "body": forms.Textarea(attrs={"class": "form-control", "rows": 5, "placeholder": "اشرح المشكلة بالتفصيل..."}),
            "attachment": forms.ClearableFileInput(attrs={"class": "form-control"}),
        }

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
        fields = ["name", "description", "price", "days_duration", "is_active"]
        widgets = {
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "اسم الخطة (مثلاً: باقة سنوية)"}),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "وصف مميزات الخطة..."}),
            "price": forms.NumberInput(attrs={"class": "form-control", "step": "0.01"}),
            "days_duration": forms.NumberInput(attrs={"class": "form-control"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "name": "اسم الخطة",
            "description": "الوصف",
            "price": "السعر (ريال)",
            "days_duration": "المدة (بالأيام)",
            "is_active": "نشط؟",
        }


class SchoolSubscriptionForm(forms.ModelForm):
    class Meta:
        model = SchoolSubscription
        fields = ["school", "plan", "start_date", "end_date", "is_active"]
        widgets = {
            "school": forms.Select(attrs={"class": "form-select"}),
            "plan": forms.Select(attrs={"class": "form-select"}),
            "start_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "end_date": forms.DateInput(attrs={"class": "form-control", "type": "date"}),
            "is_active": forms.CheckboxInput(attrs={"class": "form-check-input"}),
        }
        labels = {
            "school": "المدرسة",
            "plan": "الباقة",
            "start_date": "تاريخ البدء",
            "end_date": "تاريخ الانتهاء",
            "is_active": "نشط؟",
        }
