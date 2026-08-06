# -*- coding: utf-8 -*-
"""نماذج الاجتماعات.

الحدّ المفروض: المنظّم يدعو من يبلغه نطاقُه. مدير المدرسة يدعو منسوبيه، والوكيل
يدعو من يشرف عليهم، والمدير التنفيذي يدعو مديري مدارس مجموعته. وكلها مفروضة في
``queryset`` وفي ``clean`` معاً.
"""
from __future__ import annotations

from datetime import timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from .models import Decision, Department, Meeting, MeetingAgendaItem, MeetingMinutes, SchoolMembership, Teacher
from .permissions import is_school_manager, supervised_department_ids

__all__ = [
    "SchoolMeetingForm",
    "GroupMeetingForm",
    "AgendaItemForm",
    "MinutesForm",
    "DecisionForm",
]


class _MeetingFormBase(forms.ModelForm):
    DEFAULT_DAYS = 3

    class Meta:
        model = Meeting
        fields = ("title", "purpose", "scheduled_at", "location")
        widgets = {
            "scheduled_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
            "purpose": forms.Textarea(attrs={"rows": 3, "placeholder": "الغرض من الاجتماع…"}),
            "title": forms.TextInput(attrs={"placeholder": "مثال: اجتماع اللجنة التعليمية الأول"}),
            "location": forms.TextInput(attrs={"placeholder": "مثال: قاعة الاجتماعات"}),
        }

    def clean_scheduled_at(self):
        value = self.cleaned_data.get("scheduled_at")
        if value and value <= timezone.now() - timedelta(days=365):
            raise ValidationError("تاريخ الانعقاد بعيد جداً في الماضي.")
        return value


class SchoolMeetingForm(_MeetingFormBase):
    """اجتماع داخل المدرسة."""

    attendees = forms.ModelMultipleChoiceField(
        queryset=Teacher.objects.none(),
        label="المدعوون",
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "ادعُ مشاركاً واحداً على الأقل."},
    )

    class Meta(_MeetingFormBase.Meta):
        fields = ("title", "purpose", "department", "scheduled_at", "location")

    def __init__(self, *args, school=None, organizer=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.organizer = organizer

        self.instance.scope = Meeting.Scope.SCHOOL
        self.instance.school = school
        self.instance.organizer = organizer

        self.fields["department"].queryset = (
            Department.objects.filter(school=school, is_active=True).order_by("name")
            if school is not None
            else Department.objects.none()
        )
        self.fields["department"].required = False
        self.fields["department"].label = "اللجنة / القسم"
        self.fields["department"].help_text = (
            "يحدّد من يشرف على هذا الاجتماع ضمن نطاقه إضافةً إليك."
        )
        self.fields["attendees"].queryset = self._eligible()
        self.fields["scheduled_at"].initial = timezone.localtime() + timedelta(
            days=self.DEFAULT_DAYS
        )

    def _eligible(self):
        if self.school is None or self.organizer is None:
            return Teacher.objects.none()
        base = Teacher.objects.filter(
            is_active=True,
            school_memberships__school=self.school,
            school_memberships__is_active=True,
        ).distinct()
        if is_school_manager(self.organizer, active_school=self.school):
            return base.order_by("name")

        supervised = supervised_department_ids(self.organizer, self.school)
        if not supervised:
            return Teacher.objects.none()
        return (
            base.filter(dept_memberships__department_id__in=supervised)
            .distinct()
            .order_by("name")
        )

    def clean(self):
        cleaned = super().clean()
        department = cleaned.get("department")
        if department is not None and self.school is not None:
            if department.school_id != self.school.pk:
                raise ValidationError({"department": "هذا القسم ليس من أقسام مدرستك."})
            if not is_school_manager(self.organizer, active_school=self.school):
                if department.pk not in supervised_department_ids(self.organizer, self.school):
                    raise ValidationError({"department": "هذا القسم خارج نطاق إشرافك."})
        return cleaned


class GroupMeetingForm(_MeetingFormBase):
    """مجلس مجموعة المدارس — المدعوون مديرو مدارسها."""

    schools = forms.ModelMultipleChoiceField(
        queryset=None,
        label="المدارس المدعوّة",
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "اختر مدرسة واحدة على الأقل."},
    )

    def __init__(self, *args, group=None, organizer=None, allowed_schools=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.group = group
        self.organizer = organizer

        from .models import School

        self.instance.scope = Meeting.Scope.GROUP
        self.instance.group = group
        self.instance.organizer = organizer

        self.fields["schools"].queryset = (
            allowed_schools if allowed_schools is not None else School.objects.none()
        )
        self.fields["scheduled_at"].initial = timezone.localtime() + timedelta(
            days=self.DEFAULT_DAYS
        )

    def resolve_attendees(self) -> tuple[list, list]:
        """مديرو المدارس المختارة، وما تعذّر منها."""
        managers, unreachable = [], []
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
                managers.append(membership.teacher)
        return managers, unreachable


class AgendaItemForm(forms.ModelForm):
    class Meta:
        model = MeetingAgendaItem
        fields = ("title", "note")
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "عنوان البند"}),
            "note": forms.Textarea(attrs={"rows": 2, "placeholder": "تمهيد أو مرفق (اختياري)"}),
        }


class MinutesForm(forms.ModelForm):
    class Meta:
        model = MeetingMinutes
        fields = ("body",)
        widgets = {
            "body": forms.Textarea(
                attrs={"rows": 12, "placeholder": "ما دار في الاجتماع…"}
            )
        }
        labels = {"body": "نص المحضر"}


class DecisionForm(forms.ModelForm):
    """تسجيل قرار أو توصية.

    المسؤول والموعد اختياريان هنا وإلزاميان عند التحويل إلى تكليف — فليس كل ما
    يُقرَّر يُنفَّذ بيد شخص بعينه، والتوثيق يسبق المتابعة ولا يشترطها.
    """

    class Meta:
        model = Decision
        fields = ("kind", "title", "body", "agenda_item", "responsible", "due_at")
        widgets = {
            "title": forms.TextInput(attrs={"placeholder": "نص القرار"}),
            "body": forms.Textarea(attrs={"rows": 3, "placeholder": "تفصيل (اختياري)"}),
            "due_at": forms.DateTimeInput(attrs={"type": "datetime-local"}),
        }

    def __init__(self, *args, meeting=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.meeting = meeting

        self.fields["agenda_item"].queryset = (
            MeetingAgendaItem.objects.filter(meeting=meeting).order_by("order")
            if meeting is not None
            else MeetingAgendaItem.objects.none()
        )
        self.fields["agenda_item"].required = False
        self.fields["agenda_item"].label = "البند المرتبط"

        # المسؤول من المدعوين: قرارٌ يُسند إلى غائب عن الاجتماع لا يعرف صاحبه
        # كيف صدر ولا لماذا.
        self.fields["responsible"].queryset = (
            Teacher.objects.filter(meeting_attendances__meeting=meeting).distinct().order_by("name")
            if meeting is not None
            else Teacher.objects.none()
        )
        self.fields["responsible"].required = False
        self.fields["due_at"].required = False
        self.fields["due_at"].help_text = "مطلوب لتحويل القرار إلى تكليف متابَع."

    def clean_due_at(self):
        value = self.cleaned_data.get("due_at")
        if value and value <= timezone.now():
            raise ValidationError("موعد التنفيذ يجب أن يكون في المستقبل.")
        return value
