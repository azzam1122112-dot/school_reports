# -*- coding: utf-8 -*-
"""نموذج رفع الوثائق وتصنيفها."""
from __future__ import annotations

from django import forms
from django.core.exceptions import ValidationError

from .models import Department, Document

__all__ = ["DocumentUploadForm"]


class DocumentUploadForm(forms.ModelForm):
    class Meta:
        model = Document
        fields = ("title", "description", "academic_year", "department", "kind", "file")
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "مثال: محضر لجنة التوجيه — الجلسة الثانية"}),
            "description": forms.Textarea(
                attrs={"rows": 2, "placeholder": "وصف مختصر يعين على البحث لاحقاً"}
            ),
        }

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school

        years = []
        if school is not None:
            current = (getattr(school, "current_academic_year", "") or "").strip()
            allowed = list(getattr(school, "allowed_academic_years", None) or [])
            years = sorted({str(item) for item in allowed if item} | ({current} if current else set()), reverse=True)

        self.fields["academic_year"] = forms.ChoiceField(
            label="السنة الدراسية",
            required=True,
            choices=[("", "— اختر السنة —")] + [(item, f"{item} هـ") for item in years],
            error_messages={"required": "السنة الدراسية أول ما يُقلَّب به الأرشيف."},
        )
        if school is not None:
            self.fields["academic_year"].initial = (
                getattr(school, "current_academic_year", "") or ""
            )

        self.fields["department"].queryset = (
            Department.objects.filter(school=school, is_active=True).order_by("name")
            if school is not None
            else Department.objects.none()
        )
        self.fields["department"].required = False
        self.fields["department"].help_text = (
            "يحدّد من يراجع أرشفتها ضمن نطاقه. تركه فارغاً يجعل الاعتماد لمدير المدرسة."
        )
        self.fields["file"].help_text = "PDF أو صورة أو مستند حتى 5MB."

    def clean_department(self):
        department = self.cleaned_data.get("department")
        if department is not None and self.school is not None:
            if department.school_id != self.school.pk:
                raise ValidationError("هذا القسم ليس من أقسام مدرستك.")
        return department
