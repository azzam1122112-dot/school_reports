# -*- coding: utf-8 -*-
"""نماذج شاشات المختبر.

الحدّ الذي تفرضه: **كل اختيارٍ لشخصٍ أو صنفٍ مقصورٌ على المدرسة النشطة**،
والقيد في ``__init__`` لا في القالب — لأن القالب يُتجاوَز بطلب مُصاغ يدوياً،
فتُسنَد عهدةٌ لمنسوب مدرسةٍ أخرى.
"""
from __future__ import annotations

from django import forms

from .models import (
    Department,
    LabAsset,
    LabAssetHandover,
    LabExperiment,
    Report,
    SchoolMembership,
    Teacher,
)

__all__ = ["LabAssetForm", "LabHandoverForm", "LabExperimentForm"]


def _school_members(school):
    """منسوبو المدرسة — من يجوز أن يُسلَّم عهدةً أو يُطلب له تجربة."""
    if school is None:
        return Teacher.objects.none()
    return (
        Teacher.objects.filter(
            is_active=True,
            school_memberships__school=school,
            school_memberships__is_active=True,
            school_memberships__role_type__in=SchoolMembership.STAFF_ROLES,
        )
        .distinct()
        .order_by("name")
    )


def _configure_lab_department(form, *, school, user) -> None:
    """احصر اختيار المختبر، مع إسناد تلقائي حين لا يوجد إلا خيار واحد."""
    from .services_lab import lab_departments_for_user

    all_departments = Department.objects.filter(school=school, is_active=True)
    allowed = (
        lab_departments_for_user(user, school)
        if school is not None
        else Department.objects.none()
    )
    field = form.fields["department"]
    field.queryset = allowed
    field.required = False
    field.empty_label = "— اختر مختبر العلوم أو الحاسب —"
    field.help_text = (
        "هذا الاختيار يعزل العهدة والتجارب بين مختبر العلوم ومختبر الحاسب الآلي."
    )
    form._lab_school_has_departments = all_departments.exists()
    form._lab_allowed_departments = allowed
    form._lab_single_department = allowed.first() if allowed.count() == 1 else None
    if form._lab_single_department is not None and not form.instance.pk:
        form.initial.setdefault("department", form._lab_single_department.pk)


def _clean_lab_department(form, cleaned):
    department = cleaned.get("department")
    if department is not None:
        return department
    if form._lab_single_department is not None:
        cleaned["department"] = form._lab_single_department
        return form._lab_single_department
    if form._lab_school_has_departments:
        if form._lab_allowed_departments.exists():
            form.add_error(
                "department", "حدّد المختبر الذي يخص هذا السجل."
            )
        else:
            form.add_error(
                "department",
                "لم يُربط حساب محضّر المختبر بقسم. اربطه بقسم العلوم أو الحاسب أولاً.",
            )
    return None


class LabAssetForm(forms.ModelForm):
    class Meta:
        model = LabAsset
        fields = (
            "department",
            "name",
            "code",
            "category",
            "quantity",
            "unit",
            "condition",
            "location",
            "custodian",
            "notes",
        )
        widgets = {
            "department": forms.Select(attrs={"class": "form-control"}),
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "ميكروسكوب ضوئي"}),
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "اختياري"}),
            "category": forms.Select(attrs={"class": "form-control"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 1}),
            "unit": forms.TextInput(attrs={"class": "form-control", "placeholder": "قطعة"}),
            "condition": forms.Select(attrs={"class": "form-control"}),
            "location": forms.TextInput(attrs={"class": "form-control", "placeholder": "دولاب ٣ — الرف الأعلى"}),
            "custodian": forms.Select(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, school=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.user = user
        _configure_lab_department(self, school=school, user=user)
        self.fields["custodian"].queryset = _school_members(school)
        self.fields["custodian"].required = False
        self.fields["custodian"].empty_label = "— بلا مسؤول محدَّد —"
        self.fields["name"].help_text = "اسم واضح كما يظهر في كشف العهدة."
        self.fields["code"].help_text = "اختياري — الرقم الرسمي أو التسلسلي إن وُجد."
        self.fields["quantity"].help_text = "إجمالي الكمية المسجّلة، قطعة واحدة على الأقل."
        self.fields["location"].help_text = "حدّد الدولاب والرف لتسهيل الوصول والجرد."
        self.fields["custodian"].help_text = "يُسند تلقائياً لمحضر المختبر عند تركه فارغاً."

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        if quantity is None:
            return 0
        if int(quantity) < 1:
            raise forms.ValidationError("أدخل قطعة واحدة على الأقل.")
        return int(quantity)

    def clean(self):
        cleaned = super().clean()
        _clean_lab_department(self, cleaned)
        # تخفيض الكمية دون ما هو خارج المختبر يجعل الجرد يقول إن المُسلَّم أكثر
        # من الموجود — وهو رقمٌ لا يمكن تسويته إلا بحذف حركة وقعت فعلاً.
        if self.instance.pk:
            quantity = cleaned.get("quantity")
            if quantity is not None:
                out = self.instance.out_quantity
                if int(quantity) < out:
                    self.add_error(
                        "quantity",
                        f"لا تقلّ الكمية عن المُسلَّم حالياً ({out}). سجّل الإرجاع أولاً.",
                    )
        return cleaned


class LabHandoverForm(forms.Form):
    """تسليم صنف أو إرجاعه.

    نموذجٌ عادي لا ``ModelForm``: التحقق الحقيقي (المتاح والخارج) يعيش في
    ``clean`` النموذج وتستدعيه الخدمة، ونسخُه هنا يخلق قاعدتين تفترقان.
    """

    direction = forms.ChoiceField(
        label="الحركة",
        choices=LabAssetHandover.Direction.choices,
        widget=forms.Select(attrs={"class": "form-control", "id": "id_handover_direction"}),
    )
    person = forms.ModelChoiceField(
        queryset=Teacher.objects.none(),
        label="المستلم / المُعيد",
        required=False,
        widget=forms.Select(attrs={"class": "form-control", "id": "id_handover_person"}),
        error_messages={"invalid_choice": "هذا المستخدم ليس من منسوبي مدرستك."},
    )
    quantity = forms.IntegerField(
        label="الكمية",
        min_value=1,
        initial=1,
        widget=forms.NumberInput(
            attrs={"class": "form-control", "min": 1, "id": "id_handover_quantity"}
        ),
    )
    note = forms.CharField(
        label="ملاحظة",
        required=False,
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "اختياري",
                "id": "id_handover_note",
            }
        ),
    )

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.fields["person"].queryset = _school_members(school)

    def clean(self):
        cleaned = super().clean()
        direction = cleaned.get("direction")
        if not cleaned.get("person"):
            if direction == LabAssetHandover.Direction.OUT:
                self.add_error("person", "حدّد من تسلَّم الصنف.")
            elif direction == LabAssetHandover.Direction.IN:
                self.add_error("person", "حدّد من أعاد الصنف لتسوية عهدته بدقة.")
        return cleaned


class LabExperimentForm(forms.ModelForm):
    class Meta:
        model = LabExperiment
        fields = (
            "department",
            "title",
            "experiment_date",
            "subject",
            "class_name",
            "requested_by",
            "students_count",
            "objectives",
            "procedure",
            "materials_note",
            "assets",
            "safety_notes",
            "report",
        )
        widgets = {
            "department": forms.Select(attrs={"class": "form-control"}),
            "title": forms.TextInput(attrs={"class": "form-control", "placeholder": "استخلاص الكلوروفيل"}),
            "experiment_date": forms.DateInput(
                attrs={"class": "form-control", "type": "date"}, format="%Y-%m-%d"
            ),
            "subject": forms.TextInput(attrs={"class": "form-control", "placeholder": "أحياء"}),
            "class_name": forms.TextInput(attrs={"class": "form-control", "placeholder": "ثاني ثانوي — أ"}),
            "requested_by": forms.Select(attrs={"class": "form-control"}),
            "students_count": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "objectives": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "procedure": forms.Textarea(attrs={"class": "form-control", "rows": 5}),
            "materials_note": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "assets": forms.SelectMultiple(attrs={"class": "form-control", "size": 6}),
            "safety_notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "report": forms.Select(attrs={"class": "form-control"}),
        }

    def __init__(self, *args, school=None, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.user = user
        _configure_lab_department(self, school=school, user=user)

        self.fields["requested_by"].queryset = _school_members(school)
        self.fields["requested_by"].required = False
        self.fields["requested_by"].empty_label = "— لم يطلبها معلّم بعينه —"

        from .services_lab import assets_for_school

        selected_department = getattr(self.instance, "department", None)
        if self.is_bound:
            posted_department = str(self.data.get("department") or "").strip()
            if posted_department.isdigit():
                selected_department = self._lab_allowed_departments.filter(
                    pk=int(posted_department)
                ).first()
        if selected_department is None:
            selected_department = self._lab_single_department

        asset_queryset = (
            assets_for_school(school, user=user)
            if school is not None
            else LabAsset.objects.none()
        )
        if selected_department is not None:
            asset_queryset = asset_queryset.filter(department=selected_department)
        self.fields["assets"].queryset = asset_queryset.order_by("name")
        self.fields["assets"].required = False

        # التقرير المرتبط: تقارير هذه المدرسة وحدها. وربطُ تقرير من مدرسة أخرى
        # يجعل شواهد التجربة تُقرأ من سياق لا يخصّها.
        self.fields["report"].queryset = (
            Report.objects.filter(school=school).order_by("-report_date", "-id")
            if school is not None
            else Report.objects.none()
        )
        self.fields["report"].required = False
        self.fields["report"].empty_label = "— بلا تقرير مرتبط —"

        # المسودة عملٌ قيد الإنشاء، لذلك تقبل النقص. عند الإرسال يفرض النموذج
        # نفسه عبر ``assert_ready_for_submission`` العنوان والتاريخ والخطوات.
        for name in ("title", "experiment_date", "procedure"):
            self.fields[name].required = False

        self.fields["students_count"].required = False
        self.fields["students_count"].widget.attrs["placeholder"] = "مثال: 24"
        self.fields["students_count"].help_text = "اختياري في المسودة؛ اتركه فارغاً إن لم يُحصر بعد."
        self.fields["assets"].help_text = "اختر ما استُخدم فعلياً من عهدة المختبر."
        self.fields["objectives"].help_text = "ما الذي يُفترض أن يتعلمه الطلاب أو يلاحظوه؟"
        self.fields["procedure"].help_text = "يلزم قبل الإرسال للاعتماد. اكتب خطوات قابلة للتكرار."

        if not self.instance.pk and not self.is_bound:
            self.initial["students_count"] = None

    def clean_students_count(self):
        value = self.cleaned_data.get("students_count")
        return 0 if value in (None, "") else value

    def clean(self):
        cleaned = super().clean()
        department = _clean_lab_department(self, cleaned)
        assets = cleaned.get("assets")
        if department is not None and assets is not None:
            if assets.exclude(department=department).exists():
                self.add_error(
                    "assets", "اختر أصنافاً من عهدة المختبر المحدد فقط."
                )
        return cleaned
