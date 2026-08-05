# -*- coding: utf-8 -*-
"""نموذج مدير النظام لإضافة مدير تنفيذي وإسناد المدارس له.

الكيان الذي يُحرَّر هنا هو *المجموعة* لا المستخدم، لأن المدير التنفيذي في هذا
النظام منصبٌ داخل مجموعة مدارس: بلا مجموعة لا معنى للمنصب، ومع المجموعة يصير
المستخدم مديراً تنفيذياً بمجرد عضويته النشطة فيها. ولذلك يجمع هذا النموذج
بيانات المجموعة وبيانات مديرها وقائمة مدارسها في شاشة واحدة — فالثلاثة قرار
تنظيمي واحد، وفصلها إلى ثلاث شاشات يترك المجموعة بلا مدير أو المدير بلا مدارس.
"""
from __future__ import annotations

from django import forms
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError
from django.utils.text import slugify

from .models import School, SchoolGroup, SchoolGroupMembership, Teacher


def generate_unique_group_code(name: str, *, exclude_pk: int | None = None) -> str:
    """كود مجموعة فريد يُشتق من الاسم.

    اسمٌ عربي بالكامل يعطي slug فارغاً، فيُستبدل بأساس ثابت ثم يُرقَّم. توليد
    الكود تلقائياً مقصود: مدير النظام يضيف مجموعة باسمها، ولا يُطالَب باختراع
    معرّف تقني ليس له أثر في الواجهة.
    """
    max_length = SchoolGroup._meta.get_field("code").max_length
    base = slugify((name or "").strip(), allow_unicode=False) or "group"
    base = base[:max_length]

    taken = SchoolGroup.objects.all()
    if exclude_pk:
        taken = taken.exclude(pk=exclude_pk)

    candidate = base
    index = 2
    while taken.filter(code=candidate).exists():
        suffix = f"-{index}"
        candidate = f"{base[: max_length - len(suffix)]}{suffix}"
        index += 1
    return candidate


class SchoolChoiceField(forms.ModelMultipleChoiceField):
    """يعرض المجموعة الحالية للمدرسة في نص الخيار.

    نقل مدرسة من مجموعة إلى أخرى قرار مشروع، لكنه لا يصح أن يقع سهواً؛ فوسم
    الخيار بمجموعته الحالية هو ما يجعل النقل ظاهراً قبل الحفظ لا بعده.
    """

    current_group_pk: int | None = None

    def label_from_instance(self, obj) -> str:
        group = getattr(obj, "group", None)
        if group is not None and group.pk != self.current_group_pk:
            return f"{obj.name} — حالياً ضمن «{group.name}»"
        return obj.name


class ExecutiveDirectorForm(forms.Form):
    """إضافة/تعديل مدير تنفيذي ومجموعته ومدارسه."""

    # ── المجموعة ───────────────────────────────────────────────────────
    group_name = forms.CharField(
        label="اسم المجموعة",
        max_length=200,
        widget=forms.TextInput(
            attrs={"class": "form-control", "placeholder": "مثال: مجمع مدارس النور الأهلية"}
        ),
    )
    group_code = forms.SlugField(
        label="المعرّف (اختياري)",
        max_length=64,
        required=False,
        help_text="يُولَّد تلقائياً من الاسم إذا تُرك فارغاً.",
        widget=forms.TextInput(attrs={"class": "form-control", "dir": "ltr", "placeholder": "al-noor"}),
    )
    education_department = forms.CharField(
        label="إدارة التعليم",
        max_length=200,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "مثال: إدارة تعليم الرياض"}),
    )
    is_active = forms.BooleanField(
        label="المجموعة نشطة",
        required=False,
        initial=True,
        help_text="إيقاف المجموعة يمنع مديرها التنفيذي من لوحة المجموعة دون المساس بأي مدرسة.",
        widget=forms.CheckboxInput(attrs={"class": "form-check-input"}),
    )

    # ── المدير التنفيذي ────────────────────────────────────────────────
    director_phone = forms.CharField(
        label="جوال المدير التنفيذي",
        max_length=10,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "05XXXXXXXX",
                "dir": "ltr",
                "inputmode": "tel",
                "maxlength": "10",
            }
        ),
        help_text="إن كان الرقم لحساب قائم فسيُربط به، وإلا أُنشئ حساب جديد.",
    )
    director_name = forms.CharField(
        label="اسم المدير التنفيذي",
        max_length=150,
        required=False,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "الاسم الرباعي"}),
    )
    director_email = forms.EmailField(
        label="البريد الإلكتروني (اختياري)",
        required=False,
        widget=forms.EmailInput(attrs={"class": "form-control", "dir": "ltr"}),
    )
    director_password = forms.CharField(
        label="كلمة المرور",
        required=False,
        strip=False,
        widget=forms.PasswordInput(attrs={"class": "form-control", "autocomplete": "new-password"}),
        help_text="مطلوبة للحساب الجديد فقط. اتركها فارغة لحساب قائم حتى لا تُغيَّر كلمته.",
    )

    # ── المدارس ────────────────────────────────────────────────────────
    schools = SchoolChoiceField(
        label="المدارس المسندة",
        queryset=School.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        error_messages={"invalid_choice": "إحدى المدارس المختارة غير موجودة أو غير نشطة."},
    )
    headquarters_school = forms.ModelChoiceField(
        label="مدرسة المقر (اختياري)",
        queryset=School.objects.none(),
        required=False,
        empty_label="— بلا مقر محدد —",
        widget=forms.Select(attrs={"class": "form-select"}),
    )

    def __init__(self, *args, group: SchoolGroup | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        self.director_user: Teacher | None = None
        self.creates_user = False

        # المدارس النشطة كلها معروضة — بما فيها المرتبطة بمجموعة أخرى، لأن نقل
        # مدرسة بين مجموعتين قرار مشروع لمدير النظام. القالب يُظهر مجموعتها
        # الحالية حتى لا يقع النقل سهواً.
        available = School.objects.filter(is_active=True).select_related("group").order_by("name")
        self.fields["schools"].queryset = available
        self.fields["schools"].current_group_pk = getattr(group, "pk", None)
        self.fields["headquarters_school"].queryset = available

    # ── تحقق الحقول ────────────────────────────────────────────────────
    def clean_director_phone(self):
        phone = (self.cleaned_data.get("director_phone") or "").strip()
        if len(phone) != 10 or not phone.startswith("05") or not phone.isdigit():
            raise ValidationError("أدخل رقم جوال سعودي صحيحاً يبدأ بـ 05.")
        return phone

    def clean_group_code(self):
        code = (self.cleaned_data.get("group_code") or "").strip()
        if not code:
            return ""
        taken = SchoolGroup.objects.filter(code=code)
        if self.group is not None:
            taken = taken.exclude(pk=self.group.pk)
        if taken.exists():
            raise ValidationError("هذا المعرّف مستخدم في مجموعة أخرى.")
        return code

    def clean(self):
        cleaned = super().clean()
        phone = cleaned.get("director_phone")
        if not phone:
            return cleaned

        user = Teacher.objects.filter(phone=phone).first()
        self.director_user = user
        self.creates_user = user is None

        if user is None:
            # حساب جديد: الاسم وكلمة المرور شرطان لإنشائه، فبدونهما لا يوجد
            # مدير تنفيذي أصلاً وتُترك المجموعة بلا قيادة.
            if not (cleaned.get("director_name") or "").strip():
                self.add_error("director_name", "الاسم مطلوب لإنشاء حساب جديد.")
            password = cleaned.get("director_password") or ""
            if not password:
                self.add_error("director_password", "كلمة المرور مطلوبة لإنشاء حساب جديد.")
            else:
                try:
                    password_validation.validate_password(password)
                except ValidationError as exc:
                    self.add_error("director_password", exc)
        else:
            # منصب واحد لكل شخص: منعُ ازدواج المنصب هنا يعطي رسالة مفهومة بدل
            # أن يسقط الحفظ لاحقاً على قيد قاعدة البيانات.
            conflict = (
                SchoolGroupMembership.objects.filter(
                    user=user,
                    is_active=True,
                    role_type=SchoolGroupMembership.RoleType.EXECUTIVE_DIRECTOR,
                )
                .exclude(group=self.group)
                .select_related("group")
                .first()
            )
            if conflict is not None:
                self.add_error(
                    "director_phone",
                    f"هذا المستخدم مدير تنفيذي نشط لمجموعة «{conflict.group.name}». "
                    "أوقف عضويته هناك أولاً.",
                )

        headquarters = cleaned.get("headquarters_school")
        selected = list(cleaned.get("schools") or [])
        if headquarters is not None and headquarters not in selected:
            self.add_error("headquarters_school", "مدرسة المقر يجب أن تكون ضمن المدارس المسندة.")

        return cleaned
