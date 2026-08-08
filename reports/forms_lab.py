# -*- coding: utf-8 -*-
"""نماذج شاشات المختبر.

الحدّ الذي تفرضه: **كل اختيارٍ لشخصٍ أو صنفٍ مقصورٌ على المدرسة النشطة**،
والقيد في ``__init__`` لا في القالب — لأن القالب يُتجاوَز بطلب مُصاغ يدوياً،
فتُسنَد عهدةٌ لمنسوب مدرسةٍ أخرى.
"""
from __future__ import annotations

from django import forms

from .models import LabAsset, LabAssetHandover, LabExperiment, Report, SchoolMembership, Teacher

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


class LabAssetForm(forms.ModelForm):
    class Meta:
        model = LabAsset
        fields = (
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
            "name": forms.TextInput(attrs={"class": "form-control", "placeholder": "ميكروسكوب ضوئي"}),
            "code": forms.TextInput(attrs={"class": "form-control", "placeholder": "اختياري"}),
            "category": forms.Select(attrs={"class": "form-control"}),
            "quantity": forms.NumberInput(attrs={"class": "form-control", "min": 0}),
            "unit": forms.TextInput(attrs={"class": "form-control", "placeholder": "قطعة"}),
            "condition": forms.Select(attrs={"class": "form-control"}),
            "location": forms.TextInput(attrs={"class": "form-control", "placeholder": "دولاب ٣ — الرف الأعلى"}),
            "custodian": forms.Select(attrs={"class": "form-control"}),
            "notes": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
        }

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.fields["custodian"].queryset = _school_members(school)
        self.fields["custodian"].required = False
        self.fields["custodian"].empty_label = "— بلا مسؤول محدَّد —"

    def clean_quantity(self):
        quantity = self.cleaned_data.get("quantity")
        if quantity is None:
            return 0
        if int(quantity) < 0:
            raise forms.ValidationError("الكمية لا تكون سالبة.")
        return int(quantity)

    def clean(self):
        cleaned = super().clean()
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
        widget=forms.Select(attrs={"class": "form-control"}),
    )
    person = forms.ModelChoiceField(
        queryset=Teacher.objects.none(),
        label="المستلم",
        required=False,
        widget=forms.Select(attrs={"class": "form-control"}),
        error_messages={"invalid_choice": "هذا المستخدم ليس من منسوبي مدرستك."},
    )
    quantity = forms.IntegerField(
        label="الكمية",
        min_value=1,
        initial=1,
        widget=forms.NumberInput(attrs={"class": "form-control", "min": 1}),
    )
    note = forms.CharField(
        label="ملاحظة",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"class": "form-control", "placeholder": "اختياري"}),
    )

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.fields["person"].queryset = _school_members(school)

    def clean(self):
        cleaned = super().clean()
        # التسليم بلا مستلم لا معنى له: العهدة تُسلَّم *لأحد*. أما الإرجاع فقد
        # يقع بلا تسمية (وُجد الصنف في المختبر)، فيبقى المستلم اختيارياً فيه.
        if cleaned.get("direction") == LabAssetHandover.Direction.OUT and not cleaned.get("person"):
            self.add_error("person", "حدّد من تسلَّم الصنف.")
        return cleaned


class LabExperimentForm(forms.ModelForm):
    class Meta:
        model = LabExperiment
        fields = (
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

        self.fields["requested_by"].queryset = _school_members(school)
        self.fields["requested_by"].required = False
        self.fields["requested_by"].empty_label = "— لم يطلبها معلّم بعينه —"

        self.fields["assets"].queryset = (
            LabAsset.objects.filter(school=school, is_active=True).order_by("name")
            if school is not None
            else LabAsset.objects.none()
        )
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

        # الحقول الثلاثة التي يشترطها الإرسال تُعلَن مطلوبةً في الشاشة أيضاً،
        # فلا يكتشف صاحبها الشرط عند الإرسال بعد أن حسب العمل منتهياً.
        for name in ("title", "experiment_date", "procedure"):
            self.fields[name].required = True
