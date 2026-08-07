# -*- coding: utf-8 -*-
"""نماذج شاشة الأدوار والصلاحيات (مدير المدرسة).

الحدّ الذي تفرضه هذه النماذج: **المدير يوزّع ما دون دوره لا دوره**. فلا يُسند
دور «مدير» من هنا، ولا يمنح صلاحية غير معرَّفة في مرجع الكود، ولا يفوّض من ليس
منسوباً في مدرسته. وكل قيد منها منفَّذ في ``clean`` لا في القالب، لأن القالب
يُتجاوَز بطلب مُصاغ يدوياً.
"""
from __future__ import annotations

from datetime import timedelta

from django import forms
from django.core.exceptions import ValidationError
from django.utils import timezone

from . import capabilities as caps
from .gender_labels import school_gender_labels
from .models import Delegation, Department, SchoolMembership, StaffScope, Teacher

__all__ = ["StaffRoleAssignForm", "StaffScopeForm", "DelegationForm", "ASSIGNMENTS"]


# ما يختاره المدير من قائمة «الدور» ليس ``RoleType`` وحده. «محضر المختبر» مسمّى
# وظيفي لا دور — صلاحيته صلاحية الموظف الإداري — لكنه في نظر المدير خيار من
# خيارات الإسناد كبقيتها، وغيابُه من القائمة كان يضطره إلى إسناده معلّماً فيُعامَل
# معاملة المعلّم في الصلاحية والكشوف. فتُعرض الخيارات في قائمة واحدة، ويُترجَم كل
# خيار هنا إلى (دور، مسمّى) عند التطبيق.
#
# «مدير» ليس منها: نقل الإدارة قرار يخص مالك النظام، ولو أُتيح هنا لاستطاع
# المدير أن يعزل نفسه بنقرة.
ASSIGNMENTS: dict[str, tuple[str, str | None]] = {
    SchoolMembership.RoleType.DEPUTY: (SchoolMembership.RoleType.DEPUTY, None),
    SchoolMembership.RoleType.ADMIN_STAFF: (
        SchoolMembership.RoleType.ADMIN_STAFF,
        SchoolMembership.JobTitle.ADMIN_STAFF,
    ),
    SchoolMembership.JobTitle.LAB_TECH: (
        SchoolMembership.RoleType.ADMIN_STAFF,
        SchoolMembership.JobTitle.LAB_TECH,
    ),
    SchoolMembership.RoleType.TEACHER: (
        SchoolMembership.RoleType.TEACHER,
        SchoolMembership.JobTitle.TEACHER,
    ),
}

# الأدوار التي يجوز لمدير المدرسة إسنادها — مشتقة من الخيارات أعلاه لا مكرّرة
# عنها، فلا يبقى مصدران للحقيقة الواحدة.
ASSIGNABLE_ROLES = tuple({role for role, _ in ASSIGNMENTS.values()})


def assignment_choices(school=None) -> list[tuple[str, str]]:
    """خيارات الإسناد بمسمّياتها حسب جنس المدرسة النشطة.

    الوكيل في مدرسة بنات «وكيلة»، والمحضر «محضرة مختبر» — والتسمية تُقرأ من
    مرجع المسمّيات لا تُكتب هنا.
    """
    labels = school_gender_labels(school)
    return [
        (SchoolMembership.RoleType.DEPUTY, str(labels["deputy"])),
        (SchoolMembership.RoleType.ADMIN_STAFF, str(labels["admin_staff"])),
        (SchoolMembership.JobTitle.LAB_TECH, str(labels["lab_tech"])),
        (SchoolMembership.RoleType.TEACHER, str(labels["teacher_indefinite"])),
    ]


class StaffRoleAssignForm(forms.Form):
    """إسناد دور لمنسوب داخل المدرسة النشطة."""

    member = forms.ModelChoiceField(
        queryset=Teacher.objects.none(),
        label="المنسوب",
        error_messages={"invalid_choice": "هذا المستخدم ليس من منسوبي مدرستك."},
    )
    role_type = forms.ChoiceField(
        label="الدور",
        choices=assignment_choices,
        error_messages={"invalid_choice": "دور غير معتمد."},
    )
    keep_teaching_role = forms.BooleanField(
        label="يحتفظ بنصابه التدريسي",
        required=False,
        help_text="للوكيل أو الموظف الذي يدرّس أيضاً — يبقى له دور معلّم بجانب دوره الجديد.",
    )

    def __init__(self, *args, school=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.fields["role_type"].choices = assignment_choices(school)
        # قائمة المنسوبين مقصورة على المدرسة النشطة، فمعرّف من خارجها يُرفض
        # في ``clean`` لا في القالب.
        self.fields["member"].queryset = (
            Teacher.objects.filter(
                school_memberships__school=school,
                school_memberships__is_active=True,
                school_memberships__role_type__in=SchoolMembership.STAFF_ROLES,
            )
            .distinct()
            .order_by("name")
            if school is not None
            else Teacher.objects.none()
        )

    @property
    def selected_label(self) -> str:
        """مسمّى الخيار المُسنَد كما رآه المدير في القائمة."""
        chosen = str(self.cleaned_data.get("role_type") or "")
        for value, label in self.fields["role_type"].choices:
            if str(value) == chosen:
                return str(label)
        return chosen

    def clean(self):
        cleaned = super().clean()
        member = cleaned.get("member")
        choice = cleaned.get("role_type")
        if not member or not choice:
            return cleaned

        if self.school is None:
            raise ValidationError("لا توجد مدرسة نشطة.")

        # المدير لا يُسند لنفسه دوراً أدنى: العضوية الإدارية واحدة لكل مدرسة،
        # وتغييرها من هنا يترك المدرسة بلا مدير.
        is_manager = SchoolMembership.objects.filter(
            school=self.school,
            teacher=member,
            role_type=SchoolMembership.RoleType.MANAGER,
            is_active=True,
        ).exists()
        if is_manager:
            raise ValidationError("لا يمكن تغيير دور مدير المدرسة من هذه الشاشة.")

        return cleaned

    def apply(self, *, actor=None) -> SchoolMembership:
        """يطبّق الإسناد ويُعيد العضوية الناتجة.

        الإسناد **استبدال لا تكديس**: أدوار المنسوب السابقة تُزال ما لم يُطلب
        الاحتفاظ بالنصاب التدريسي صراحةً. لولا ذلك لتراكمت الأدوار بالنقر
        المتكرر حتى يصير الجميع كل شيء.

        ويُكتب المسمّى الوظيفي مع الدور لا بعده في شاشة أخرى: إسناد «محضر
        المختبر» بلا مسمّاه يجعله موظفاً إدارياً في كل كشف وتصدير، وهو ما كان
        يُفقده اسمه.
        """
        member = self.cleaned_data["member"]
        choice = self.cleaned_data["role_type"]
        role, job_title = ASSIGNMENTS[choice]
        keep_teaching = bool(self.cleaned_data.get("keep_teaching_role"))

        keep = {role}
        if keep_teaching and role != SchoolMembership.RoleType.TEACHER:
            keep.add(SchoolMembership.RoleType.TEACHER)

        # الأدوار الزائدة تُحذف، ونطاقاتها تسقط معها بـ CASCADE — وهو الصحيح:
        # نطاق وكيلٍ لم يعد وكيلاً ليس له معنى.
        SchoolMembership.objects.filter(
            school=self.school,
            teacher=member,
            role_type__in=SchoolMembership.STAFF_ROLES,
        ).exclude(role_type__in=keep).delete()

        membership = None
        for wanted in sorted(keep):
            obj, _ = SchoolMembership.objects.get_or_create(
                school=self.school,
                teacher=member,
                role_type=wanted,
                defaults={"is_active": True},
            )
            updates = []
            if not obj.is_active:
                obj.is_active = True
                updates.append("is_active")

            # عضوية «النصاب التدريسي» المصاحبة مسمّاها معلّم دائماً؛ والمسمّى
            # المختار يُكتب على العضوية الأصلية. أما الوكيل فلا مسمّى وظيفياً
            # له في القائمة، فيُترك مسمّاه كما هو ولا يُصفَّر بالإسناد.
            wanted_title = (
                SchoolMembership.JobTitle.TEACHER
                if wanted == SchoolMembership.RoleType.TEACHER and wanted != role
                else job_title
            )
            if wanted_title and obj.job_title != wanted_title:
                obj.job_title = wanted_title
                updates.append("job_title")

            if updates:
                obj.save(update_fields=updates)
            if wanted == role:
                membership = obj

        return membership


class StaffScopeForm(forms.ModelForm):
    """ضبط نطاق منسوب: المجال، الأقسام، الصلاحيات."""

    template_code = forms.ChoiceField(
        label="القالب المعتمد",
        required=False,
        help_text="يملأ الصلاحيات دفعةً واحدة. يمكنك تعديلها بعده.",
    )
    capabilities = forms.MultipleChoiceField(
        label="الصلاحيات",
        required=False,
        widget=forms.CheckboxSelectMultiple,
    )

    class Meta:
        model = StaffScope
        fields = ("domain", "departments", "capabilities", "template_code")
        widgets = {"departments": forms.CheckboxSelectMultiple}

    def __init__(self, *args, school=None, role_type="", **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.role_type = str(role_type or "")

        self.fields["departments"].queryset = (
            Department.objects.filter(school=school, is_active=True).order_by("name")
            if school is not None
            else Department.objects.none()
        )
        self.fields["departments"].required = False

        allowed = caps.capabilities_for_role(self.role_type)
        self.fields["capabilities"].choices = [(item.code, item.label) for item in allowed]

        self.fields["template_code"].choices = [("", "بلا قالب — اختيار يدوي")] + [
            (item.code, item.label) for item in caps.templates_for_role(self.role_type)
        ]

        # مجال الوكالة لا معنى له لغير الوكيل، فيُزال من النموذج بدل أن يُعرض
        # معطّلاً ويُرسَل فارغاً فيبدو كأن المدير اختار «بلا مجال».
        if self.role_type != SchoolMembership.RoleType.DEPUTY:
            self.fields.pop("domain", None)
        else:
            self.fields["domain"].required = False

    def clean_capabilities(self):
        return caps.sanitize(self.cleaned_data.get("capabilities"), role=self.role_type or None)

    def clean(self):
        cleaned = super().clean()
        template = (cleaned.get("template_code") or "").strip()
        if template:
            known = caps.TEMPLATES_BY_CODE.get(template)
            if known is None or known.role != self.role_type:
                raise ValidationError({"template_code": "قالب غير معتمد لهذا الدور."})
        return cleaned


class DelegationForm(forms.ModelForm):
    """منح تفويض مؤقت من المدير لأحد منسوبيه."""

    DEFAULT_DAYS = 7

    class Meta:
        model = Delegation
        fields = ("delegate", "capabilities", "reason", "starts_at", "ends_at")
        widgets = {
            # الصيغة مثبَّتة على ما يقرأه ``datetime-local``: بدونها يُطبع الوقت
            # الابتدائي بصيغة جانغو العامة فيرفضه المتصفح ويعرض حقلاً فارغاً،
            # فيظن المدير أن عليه كتابة التاريخين من الصفر.
            "starts_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "ends_at": forms.DateTimeInput(
                attrs={"type": "datetime-local"}, format="%Y-%m-%dT%H:%M"
            ),
            "reason": forms.TextInput(attrs={"placeholder": "مثال: إجازة المدير من 5 إلى 12"}),
        }

    capabilities = forms.MultipleChoiceField(
        label="الصلاحيات المفوَّضة",
        required=True,
        widget=forms.CheckboxSelectMultiple,
        error_messages={"required": "اختر صلاحية واحدة على الأقل — التفويض الفارغ لا معنى له."},
    )

    def __init__(self, *args, school=None, delegator=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.school = school
        self.delegator = delegator

        self.fields["delegate"].queryset = (
            Teacher.objects.filter(
                school_memberships__school=school,
                school_memberships__is_active=True,
                school_memberships__role_type__in=SchoolMembership.STAFF_ROLES,
            )
            .distinct()
            .order_by("name")
            if school is not None
            else Teacher.objects.none()
        )
        self.fields["delegate"].label = "المفوَّض إليه"

        # التفويض ينوب عن المدير، فلا يُفوَّض إلا ما هو نافذ فعلاً. الصلاحيات
        # المعلَّقة تُخفى هنا — تفويض صلاحية جوفاء يعطي المدير طمأنينة كاذبة
        # وقت غيابه، وهو أسوأ ما يمكن أن يفعله تفويض.
        self.fields["capabilities"].choices = [
            (item.code, item.label) for item in caps.ALL if item.available
        ]

        now = timezone.localtime()
        self.fields["starts_at"].initial = now
        self.fields["ends_at"].initial = now + timedelta(days=self.DEFAULT_DAYS)
        self.fields["ends_at"].help_text = "التفويض بلا نهاية ليس تفويضاً — المدة إلزامية."

    def clean_capabilities(self):
        return caps.sanitize(self.cleaned_data.get("capabilities"))

    def clean(self):
        cleaned = super().clean()
        instance = self.instance
        instance.school = self.school
        instance.delegator = self.delegator
        return cleaned
