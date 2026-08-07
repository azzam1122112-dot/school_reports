# -*- coding: utf-8 -*-
"""نماذج التكليفات.

الحدّ الذي تفرضه: المكلِّف لا يكلّف من هو خارج نطاقه. ونطاق التكليف داخل
المدرسة هو **المدرسة**: كل منسوبيها معلماً كان أو وكيلاً أو موظفاً إدارياً.
أما المدير التنفيذي فيكلّف مديري مدارس مجموعته وحدهم — وذلك مفروض في
``queryset`` وفي ``clean`` معاً، لأن الأول يحسّن العرض والثاني يمنع الطلب
المُصاغ يدوياً.
"""
from __future__ import annotations

from datetime import timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import (  # noqa: F401
    Assignment,
    Department,
    DepartmentMembership,
    SchoolMembership,
    Teacher,
)
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

    #: ترتيب عرض الأدوار — الأخصّ أولاً، فالوكيل يُعرَّف بوكالته لا بنصابه
    #: التدريسي، والرجل الواحد قد يحمل الدورين معاً.
    ROLE_ORDER: tuple[str, ...] = (
        SchoolMembership.RoleType.DEPUTY,
        SchoolMembership.RoleType.ADMIN_STAFF,
        SchoolMembership.RoleType.TEACHER,
    )

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
        self.fields["department"].queryset = self._eligible_departments()
        self.fields["department"].required = False
        self.fields["department"].help_text = (
            "اختياري، ولا يحدّ من تُكلّفه — إنما يحدّد من يراجع التنفيذ ضمن نطاقه. "
            "تركه فارغاً يجعل المراجعة لك ولمدير المدرسة."
        )
        self.fields["due_at"].initial = timezone.localtime() + timedelta(days=self.DEFAULT_DAYS)
        self.fields["min_evidence_count"].required = False

    def _eligible_assignees(self):
        """من يجوز تكليفهم: منسوبو المدرسة جميعاً.

        معلماً كان أو وكيلاً أو موظفاً إدارياً. وكان النطاق محصوراً بأقسام
        المكلِّف، فترتّب عليه منعان بلا مقابل: الموظف الإداري الذي لا قسم له لم
        يكن قابلاً للتكليف أصلاً، والوكيل الذي لم تُسنَد إليه أقسام بعد لم يكن
        يكلّف أحداً. والتكليف عبءٌ يُسنَد لا صلاحيةٌ تُمنح، ومن أصدره مثبَّت في
        ``issuer`` ويتحمّل تبعته — فالقيد كان يمنع العمل ولا يحمي منه شيئاً.

        ويُستثنى المكلِّف نفسه: من أصدر التكليف هو من يعتمد تنفيذه، وقاعدة «لا
        يعتمد أحد عمله» تجعل تكليفه لنفسه عملاً لا مخرج له.
        """
        if self.school is None or self.issuer is None:
            return Teacher.objects.none()

        return (
            Teacher.objects.filter(
                is_active=True,
                school_memberships__school=self.school,
                school_memberships__is_active=True,
                school_memberships__role_type__in=SchoolMembership.STAFF_ROLES,
            )
            .exclude(pk=self.issuer.pk)
            .distinct()
            .order_by("name")
        )

    def _eligible_departments(self):
        """الأقسام المعروضة للاختيار كقسم معنيّ بالمراجعة.

        تُقصر على أقسام نطاق المكلِّف لغير المدير، لأن ``clean`` يرفضها بعد
        الإرسال على أي حال — وعرضُ خيارٍ يُرفض بعد ملء النموذج كله عيبٌ في
        العرض لا حماية.
        """
        if self.school is None:
            return Department.objects.none()

        departments = Department.objects.filter(
            school=self.school, is_active=True
        ).order_by("name")
        if self.issuer is None or is_school_manager(self.issuer, active_school=self.school):
            return departments
        return departments.filter(
            pk__in=supervised_department_ids(self.issuer, self.school)
        )

    def assignee_options(self):
        """خيارات المكلَّفين ومعها دور كلٍّ منهم وأقسامه.

        ``ModelMultipleChoiceField`` لا يوصل إلى القالب غير اسمٍ ومربّع، واختيار
        عشرين اسماً متشابهاً بلا دور ولا قسم اختيارٌ بالحدس. وتُبنى هنا
        باستعلامين اثنين لا باستعلام لكل اسم.
        """
        if getattr(self, "_assignee_options", None) is not None:
            return self._assignee_options

        people = list(self.fields["assignees"].queryset)
        if not people:
            self._assignee_options = []
            return self._assignee_options

        ids = [person.pk for person in people]

        roles: dict[int, set[str]] = {}
        for teacher_id, role_type in SchoolMembership.objects.filter(
            school=self.school,
            teacher_id__in=ids,
            is_active=True,
            role_type__in=SchoolMembership.STAFF_ROLES,
        ).values_list("teacher_id", "role_type"):
            roles.setdefault(teacher_id, set()).add(role_type)

        departments: dict[int, list[str]] = {}
        for teacher_id, name in (
            DepartmentMembership.objects.filter(
                teacher_id__in=ids,
                department__school=self.school,
                department__is_active=True,
            )
            .order_by("department__name")
            .values_list("teacher_id", "department__name")
        ):
            departments.setdefault(teacher_id, []).append(name)

        labels = dict(SchoolMembership.RoleType.choices)
        selected = {str(value) for value in (self["assignees"].value() or [])}

        options = []
        for person in people:
            held = roles.get(person.pk, set())
            role = next(
                (item for item in self.ROLE_ORDER if item in held),
                SchoolMembership.RoleType.TEACHER,
            )
            name = (getattr(person, "name", "") or str(person)).strip()
            options.append(
                {
                    "value": person.pk,
                    "id": f"assignee-{person.pk}",
                    "name": name,
                    "role": role,
                    "role_label": labels.get(role, ""),
                    "departments": departments.get(person.pk, []),
                    "checked": str(person.pk) in selected,
                    "initial": name[:1] or "؟",
                }
            )

        order = {role: index for index, role in enumerate(self.ROLE_ORDER)}
        options.sort(key=lambda item: (order.get(item["role"], 9), item["name"]))
        self._assignee_options = options
        return self._assignee_options

    def assignee_role_counts(self):
        """عدد المتاحين من كل دور — ليحمل زرّ التصفية رقمه بدل أن يكون وعداً."""
        options = self.assignee_options()
        labels = dict(SchoolMembership.RoleType.choices)
        counts = []
        for role in self.ROLE_ORDER:
            total = sum(1 for option in options if option["role"] == role)
            if total:
                counts.append({"role": role, "label": labels.get(role, ""), "count": total})
        return counts

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
