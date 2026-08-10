# -*- coding: utf-8 -*-
"""المرجع الوحيد لخيارات إسناد منسوبي المدرسة وكيفية تطبيقها.

يرى مدير المدرسة أربع صفات مفهومة: معلّم، وكيل، موظف إداري، ومحضّر مختبر.
أما قاعدة البيانات فتفصل بين ``role_type`` (مصدر الصلاحية) و``job_title``
(المسمّى التنظيمي). هذا الملف هو الحدّ الذي يترجم بين الواجهتين، وتستخدمه
شاشة إضافة المنسوب وشاشة الأدوار والصلاحيات معاً.
"""
from __future__ import annotations

from dataclasses import dataclass

from django.core.exceptions import ValidationError

from .gender_labels import school_gender_labels
from .models import SchoolMembership


@dataclass(frozen=True, slots=True)
class StaffAssignmentSpec:
    code: str
    role_type: str
    job_title: str | None
    label_key: str
    icon: str
    description: str
    supports_teaching_load: bool = False
    requires_scope: bool = False


STAFF_ASSIGNMENTS: tuple[StaffAssignmentSpec, ...] = (
    StaffAssignmentSpec(
        code=SchoolMembership.RoleType.TEACHER,
        role_type=SchoolMembership.role_for_job_title(
            SchoolMembership.JobTitle.TEACHER
        ),
        job_title=SchoolMembership.JobTitle.TEACHER,
        label_key="teacher_indefinite",
        icon="fa-solid fa-chalkboard-user",
        description="يوثّق أعماله المهنية وملف إنجازه وينفّذ التكليفات.",
    ),
    StaffAssignmentSpec(
        code=SchoolMembership.RoleType.DEPUTY,
        role_type=SchoolMembership.RoleType.DEPUTY,
        job_title=None,
        label_key="deputy",
        icon="fa-solid fa-user-tie",
        description="إشراف ومراجعة ضمن نطاق تحدّده، دون اعتماد نهائي.",
        supports_teaching_load=True,
        requires_scope=True,
    ),
    StaffAssignmentSpec(
        code=SchoolMembership.RoleType.ADMIN_STAFF,
        role_type=SchoolMembership.role_for_job_title(
            SchoolMembership.JobTitle.ADMIN_STAFF
        ),
        job_title=SchoolMembership.JobTitle.ADMIN_STAFF,
        label_key="admin_staff",
        icon="fa-solid fa-user-gear",
        description="إعداد وتنفيذ وتوثيق، ويرفع العمل للمراجعة.",
        supports_teaching_load=True,
        requires_scope=True,
    ),
    StaffAssignmentSpec(
        code=SchoolMembership.JobTitle.LAB_TECH,
        role_type=SchoolMembership.role_for_job_title(
            SchoolMembership.JobTitle.LAB_TECH
        ),
        job_title=SchoolMembership.JobTitle.LAB_TECH,
        label_key="lab_tech",
        icon="fa-solid fa-flask-vial",
        description="تجهيز المختبر وتوثيق تجاربه وعُهدته بصلاحية إدارية.",
        supports_teaching_load=True,
        requires_scope=True,
    ),
)

STAFF_ASSIGNMENTS_BY_CODE = {item.code: item for item in STAFF_ASSIGNMENTS}

# واجهة توافقية للاختبارات والكود الذي يحتاج الترجمة المجردة فقط.
ASSIGNMENTS: dict[str, tuple[str, str | None]] = {
    item.code: (item.role_type, item.job_title) for item in STAFF_ASSIGNMENTS
}
ASSIGNABLE_ROLES = tuple({item.role_type for item in STAFF_ASSIGNMENTS})


def get_assignment(code: str) -> StaffAssignmentSpec:
    try:
        return STAFF_ASSIGNMENTS_BY_CODE[str(code or "")]
    except KeyError as exc:
        raise ValidationError("دور غير معتمد.") from exc


def assignment_cards(school=None) -> list[dict]:
    """خيارات جاهزة للعرض، بمسمّيات توافق جنس المدرسة النشطة."""
    labels = school_gender_labels(school)
    return [
        {
            "code": item.code,
            "label": str(labels[item.label_key]),
            "icon": item.icon,
            "description": item.description,
            "supports_teaching_load": item.supports_teaching_load,
            "requires_scope": item.requires_scope,
        }
        for item in STAFF_ASSIGNMENTS
    ]


def assignment_choices(school=None) -> list[tuple[str, str]]:
    return [(item["code"], item["label"]) for item in assignment_cards(school)]


def target_roles(code: str, *, keep_teaching_role: bool = False) -> set[str]:
    assignment = get_assignment(code)
    roles = {assignment.role_type}
    if keep_teaching_role and assignment.supports_teaching_load:
        roles.add(SchoolMembership.RoleType.TEACHER)
    return roles


def assignment_matches(
    *, school, member, code: str, keep_teaching_role: bool = False
) -> bool:
    """هل العضويات النشطة تطابق اختيار المدير تماماً، دوراً ومسمّى؟"""
    assignment = get_assignment(code)
    memberships = list(
        SchoolMembership.objects.filter(
            school=school,
            teacher=member,
            is_active=True,
            role_type__in=SchoolMembership.STAFF_ROLES,
        )
    )
    if {item.role_type for item in memberships} != target_roles(
        code, keep_teaching_role=keep_teaching_role
    ):
        return False

    primary = next(
        (item for item in memberships if item.role_type == assignment.role_type), None
    )
    return bool(
        primary
        and (
            assignment.job_title is None
            or primary.job_title == assignment.job_title
        )
    )


def apply_staff_assignment(
    *,
    school,
    member,
    code: str,
    keep_teaching_role: bool = False,
    actor=None,
) -> SchoolMembership:
    """استبدل تكليف المنسوب واكتب الدور والمسمّى كوحدة واحدة.

    ``actor`` مقبول لتثبيت عقد الخدمة مع المستدعين؛ وسجل التدقيق القائم يلتقط
    عمليات الحفظ والحذف من الطلب الحالي تلقائياً.
    """
    del actor  # يسجّل middleware الفاعل من الطلب الحالي.
    if school is None:
        raise ValidationError("لا توجد مدرسة نشطة.")

    if SchoolMembership.objects.filter(
        school=school,
        teacher=member,
        role_type=SchoolMembership.RoleType.MANAGER,
        is_active=True,
    ).exists():
        raise ValidationError("لا يمكن تغيير دور مدير المدرسة من هذه الشاشة.")

    assignment = get_assignment(code)
    keep = target_roles(code, keep_teaching_role=keep_teaching_role)

    # الإسناد استبدال لا تكديس. وتسقط نطاقات الدور القديم معه بـ CASCADE.
    SchoolMembership.objects.filter(
        school=school,
        teacher=member,
        role_type__in=SchoolMembership.STAFF_ROLES,
    ).exclude(role_type__in=keep).delete()

    primary = None
    for wanted in sorted(keep):
        membership, _ = SchoolMembership.objects.get_or_create(
            school=school,
            teacher=member,
            role_type=wanted,
            defaults={"is_active": True},
        )
        updates = []
        if not membership.is_active:
            membership.is_active = True
            updates.append("is_active")

        wanted_title = (
            SchoolMembership.JobTitle.TEACHER
            if wanted == SchoolMembership.RoleType.TEACHER
            and wanted != assignment.role_type
            else assignment.job_title
        )
        if wanted_title and membership.job_title != wanted_title:
            membership.job_title = wanted_title
            updates.append("job_title")

        if updates:
            membership.save(update_fields=updates)
        if wanted == assignment.role_type:
            primary = membership

    if primary is None:  # تحصين لعقد النوع؛ لا يمكن بلوغه بكتالوج صحيح.
        raise ValidationError("تعذّر إنشاء عضوية الدور المختار.")
    return primary
