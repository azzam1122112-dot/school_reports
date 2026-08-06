# -*- coding: utf-8 -*-
"""نماذج التكليفات.

الحدّ الذي تفرضه: المكلِّف لا يكلّف من هو خارج نطاقه. مدير المدرسة يكلّف
منسوبيه، والوكيل يكلّف من يشرف عليهم في أقسامه، والمدير التنفيذي يكلّف مديري
مدارس مجموعته — وكلها مفروضة في ``queryset`` وفي ``clean`` معاً، لأن الأول
يحسّن العرض والثاني يمنع الطلب المُصاغ يدوياً.
"""
from __future__ import annotations

from datetime import timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Assignment, Department, SchoolMembership, Teacher  # noqa: F401
from .permissions import is_school_manager, supervised_department_ids

__all__ = [
    "SchoolAssignmentForm",
    "GroupAssignmentForm",
    "ProgressUpdateForm",
    "EvidenceUploadForm",
]


class SchoolAssignmentForm(forms.ModelForm):
    """إصدار تكليف داخل المدرسة."""

    DEFAULT_DAYS = 7

    assignees = forms.ModelMultipleChoiceField(
        queryset=Teacher.objects.none(),
        label="المكلَّفون",
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "اختر مكلَّفاً واحداً على الأقل."},
    )

    class Meta:
        model = Assignment
        fields = (
            "title",
            "description",
            "department",
            "priority",
            "due_at",
            "requires_evidence",
            "min_evidence_count",
        )
        widgets = {
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "description": forms.Textarea(attrs={"rows": 4, "placeholder": "المطلوب بالتفصيل…"}),
            "title": forms.TextInput(attrs={"placeholder": "مثال: رفع تقرير الأسبوع التدريبي"}),
        }

    def __init__(self, *args, school=None, issuer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.issuer = issuer

        # السياق يُثبَّت على النسخة قبل التحقق لا بعده: ``ModelForm`` يستدعي
        # ``Assignment.clean`` أثناء ``is_valid``، وقاعدةُ «التكليف داخل المدرسة
        # يحتاج مدرسة» كانت تفشل حينها لأن العرض يضبط المدرسة بعد الحفظ. وضبطها
        # هنا يجعل النموذج والعرض يتحققان من الشيء نفسه.
        self.instance.scope = Assignment.Scope.SCHOOL
        self.instance.school = school
        self.instance.issuer = issuer

        self.fields["assignees"].queryset = self._eligible_assignees()
        self.fields["department"].queryset = (
            Department.objects.filter(school=school, is_active=True).order_by("name")
            if school is not None
            else Department.objects.none()
        )
        self.fields["department"].required = False
        self.fields["department"].help_text = (
            "يحدّد من يراجع التنفيذ ضمن نطاقه. تركه فارغاً يجعل المراجعة لك ولمدير المدرسة."
        )
        self.fields["due_at"].initial = timezone.localtime() + timedelta(days=self.DEFAULT_DAYS)
        self.fields["min_evidence_count"].required = False

    def _eligible_assignees(self):
        """من يجوز تكليفهم.

        المدير يكلّف كل منسوبيه. وغيره — الوكيل — يكلّف من يقعون في أقسام
        نطاقه وحدهم؛ ونطاقٌ بلا أقسام لا يكلّف أحداً.
        """
        if self.school is None or self.issuer is None:
            return Teacher.objects.none()

        base = Teacher.objects.filter(
            is_active=True,
            school_memberships__school=self.school,
            school_memberships__is_active=True,
            school_memberships__role_type__in=SchoolMembership.STAFF_ROLES,
        ).distinct()

        if is_school_manager(self.issuer, active_school=self.school):
            return base.order_by("name")

        supervised = supervised_department_ids(self.issuer, self.school)
        if not supervised:
            return Teacher.objects.none()
        return base.filter(dept_memberships__department_id__in=supervised).distinct().order_by("name")

    def clean_due_at(self):
        value = self.cleaned_data.get("due_at")
        if value and value <= timezone.now():
            raise ValidationError("موعد التسليم يجب أن يكون في المستقبل.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("requires_evidence"):
            count = cleaned.get("min_evidence_count")
            if not count or int(count) < 1:
                cleaned["min_evidence_count"] = 1

        department = cleaned.get("department")
        if department is not None and self.school is not None and department.school_id != self.school.pk:
            raise ValidationError({"department": "هذا القسم ليس من أقسام مدرستك."})

        # الوكيل لا يكلّف خارج أقسام نطاقه ولو أُرسل معرّف قسم آخر يدوياً.
        if (
            department is not None
            and self.issuer is not None
            and not is_school_manager(self.issuer, active_school=self.school)
        ):
            if department.pk not in supervised_department_ids(self.issuer, self.school):
                raise ValidationError({"department": "هذا القسم خارج نطاق إشرافك."})

        return cleaned


class ProgressUpdateForm(forms.Form):
    """تحديث نسبة الإنجاز وملاحظات التنفيذ."""

    percent = forms.IntegerField(
        label="نسبة الإنجاز",
        min_value=0,
        max_value=100,
        widget=forms.NumberInput(attrs={"step": 5}),
        error_messages={
            "min_value": "نسبة الإنجاز بين 0 و100.",
            "max_value": "نسبة الإنجاز بين 0 و100.",
        },
    )
    note = forms.CharField(
        label="ملاحظات التنفيذ",
        required=False,
        widget=forms.Textarea(attrs={"rows": 3, "placeholder": "ما أُنجز وما تبقّى…"}),
    )


class EvidenceUploadForm(forms.Form):
    """رفع شاهد على التنفيذ."""

    file = forms.FileField(label="الشاهد")
    note = forms.CharField(
        label="وصف الشاهد",
        required=False,
        max_length=255,
        widget=forms.TextInput(attrs={"placeholder": "مثال: صورة من محضر التسليم"}),
    )


class GroupAssignmentForm(forms.ModelForm):
    """إصدار تكليف من المدير التنفيذي على مدارس مجموعته.

    **التكليف يصل مدير المدرسة لا المدرسة.** المدرسة كيان لا يستقبل ولا ينفّذ؛
    والمسؤول عنها مديرها. فالنموذج يستقبل مدارس ويحلّها إلى أشخاص عند الإصدار،
    فتعمل عليها دورة الاعتماد وقوائم المتأخرات نفسها بلا استثناء.

    ومدرسةٌ بلا مدير نشط تُستبعد صراحةً مع بيان السبب — بدل أن يصدر التكليف
    ويختفي بلا مستقبِل.
    """

    DEFAULT_DAYS = 14

    schools = forms.ModelMultipleChoiceField(
        queryset=None,
        label="المدارس المشمولة",
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "اختر مدرسة واحدة على الأقل."},
    )

    class Meta:
        model = Assignment
        fields = ("title", "description", "priority", "due_at", "requires_evidence", "min_evidence_count")
        widgets = {
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "description": forms.Textarea(attrs={"rows": 4, "placeholder": "المطلوب من كل مدرسة…"}),
            "title": forms.TextInput(attrs={"placeholder": "مثال: رفع خطة التحسين للفصل الثاني"}),
        }

    def __init__(self, *args, group=None, issuer=None, allowed_schools=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        self.issuer = issuer

        from .models import School

        self.fields["schools"].queryset = (
            allowed_schools if allowed_schools is not None else School.objects.none()
        )

        # السياق مثبَّت على النسخة قبل التحقق — كما في نموذج تكليف المدرسة.
        self.instance.scope = Assignment.Scope.GROUP
        self.instance.group = group
        self.instance.issuer = issuer

        self.fields["due_at"].initial = timezone.localtime() + timedelta(days=self.DEFAULT_DAYS)
        self.fields["min_evidence_count"].required = False

    def clean_due_at(self):
        value = self.cleaned_data.get("due_at")
        if value and value <= timezone.now():
            raise ValidationError("موعد التسليم يجب أن يكون في المستقبل.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("requires_evidence"):
            count = cleaned.get("min_evidence_count")
            if not count or int(count) < 1:
                cleaned["min_evidence_count"] = 1
        return cleaned

    def resolve_recipients(self) -> tuple[list, list]:
        """يحلّ المدارس المختارة إلى (مدير، مدرسة)، ويفصل ما تعذّر.

        يُعيد ``(recipients, unreachable)`` — والثاني يُعرض للمدير التنفيذي
        لأن صمتَ النظام عن مدرسة لم يصلها التكليف أسوأ من رفض الإصدار كله.
        """
        recipients = []
        unreachable = []
        for school in self.cleaned_data.get("schools", []):
            membership = (
                SchoolMembership.objects.filter(
                    school=school,
                    role_type=SchoolMembership.RoleType.MANAGER,
                    is_active=True,
                )
                .select_related("teacher")
                .first()
            )
            if membership is None:
                unreachable.append(school)
            else:
                recipients.append((membership.teacher, school))
        return recipients, unreachable
