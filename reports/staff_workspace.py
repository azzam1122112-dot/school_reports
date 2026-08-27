# -*- coding: utf-8 -*-
"""Build role-aware workspaces for school deputies and administrative staff.

The school role is only the starting point. Scoped oversight tools come from the
membership scope or an active delegation, while personal execution tools remain
available to the staff member.  Keeping that distinction in one builder avoids
presenting a teacher dashboard to every non-manager role.
"""
from __future__ import annotations

from django.urls import reverse

from . import capabilities as caps
from .models import DepartmentMembership, SchoolMembership, StaffScope
from .permissions import active_delegations


EMPTY_STAFF_WORKSPACES = {
    "active_school_role_labels": [],
    "has_multiple_active_school_roles": False,
    "has_teacher_role": False,
    "is_deputy_workspace": False,
    "deputy_domain_label": "",
    "deputy_template_label": "",
    "deputy_scope_names": [],
    "deputy_capability_labels": [],
    "deputy_capability_count": 0,
    "deputy_workspace_actions": [],
    "deputy_needs_setup": False,
    "deputy_scope_missing": False,
    "deputy_has_temporary_delegation": False,
    "is_admin_staff_workspace": False,
    "is_lab_technician_workspace": False,
    "admin_staff_template_label": "",
    "admin_staff_scope_names": [],
    "admin_staff_capability_labels": [],
    "admin_staff_capability_count": 0,
    "admin_staff_core_actions": [],
    "admin_staff_workspace_actions": [],
    "admin_staff_scope_missing": False,
    "admin_staff_has_temporary_delegation": False,
    "admin_staff_has_enhanced_permissions": False,
}


_DEPUTY_ACTIONS = (
    (
        caps.VIEW_SCHOOL_DASHBOARD,
        "مؤشرات نطاقي",
        "صورة مركّزة لأداء الأقسام التي تشرف عليها",
        "fa-gauge-high",
        "reports:staff_dashboard",
    ),
    (
        caps.REVIEW_REPORTS,
        "تقارير نطاقي",
        "استعراض تقارير الأقسام ومراجعة ما ينتظر قرارك",
        "fa-file-shield",
        "reports:admin_reports",
    ),
    (
        caps.VIEW_ACHIEVEMENTS,
        "ملفات الإنجاز",
        "متابعة ملفات المنسوبين داخل نطاقك",
        "fa-folder-open",
        "reports:achievement_school_files",
    ),
    (
        caps.HANDLE_REQUESTS,
        "طلبات نطاقي",
        "معالجة الطلبات المحالة إلى أقسامك",
        "fa-list-check",
        "reports:manager_school_tickets",
    ),
    (
        caps.ASSIGN_TASKS,
        "إدارة التكليفات",
        "إصدار التكليفات ومتابعة تنفيذها وشواهدها",
        "fa-diagram-project",
        "reports:assignment_board",
    ),
    (
        caps.MANAGE_MEETINGS,
        "إدارة الاجتماعات",
        "تنظيم الاجتماعات وتحرير محاضرها",
        "fa-users-rectangle",
        "reports:meeting_list",
    ),
    (
        caps.TRACK_PLANS,
        "متابعة الخطط",
        "متابعة مهام الخطط والمبادرات في نطاقك",
        "fa-compass-drafting",
        "reports:plan_list",
    ),
    (
        caps.DRAFT_CIRCULARS,
        "مسودات التعاميم",
        "إعداد المسودات ورفعها للمدير للنشر",
        "fa-file-pen",
        "reports:circular_draft_list",
    ),
    (
        caps.ARCHIVE_DOCUMENTS,
        "أرشيف الوثائق",
        "تصنيف الوثائق ومراجعة أرشفتها",
        "fa-folder-tree",
        "reports:document_archive",
    ),
    (
        caps.VIEW_AUDIT_LOG,
        "سجل النطاق",
        "مراجعة سجل الإجراءات ضمن اختصاصك",
        "fa-clock-rotate-left",
        "reports:school_audit_logs",
    ),
    (
        caps.MANAGE_LAB,
        "متابعة المختبر",
        "متابعة العهدة والتجارب وما يحتاج انتباهاً",
        "fa-flask",
        "reports:lab_dashboard",
    ),
)


_ADMIN_CAPABILITY_ACTIONS = (
    (
        caps.VIEW_SCHOOL_DASHBOARD,
        "مؤشرات نطاقي",
        "متابعة المؤشرات المسموح بها داخل الأقسام المسندة",
        "fa-chart-line",
        "reports:staff_dashboard",
    ),
    (
        caps.REVIEW_REPORTS,
        "مراجعة تقارير النطاق",
        "مراجعة التقارير وإعادتها للاستكمال دون اعتماد نهائي",
        "fa-file-circle-check",
        "reports:admin_reports",
    ),
    (
        caps.VIEW_ACHIEVEMENTS,
        "متابعة ملفات الإنجاز",
        "الاطلاع على ملفات المنسوبين المشمولين بالنطاق",
        "fa-folder-open",
        "reports:achievement_school_files",
    ),
    (
        caps.HANDLE_REQUESTS,
        "طلبات نطاقي",
        "معالجة الطلبات المحالة إلى الأقسام المسندة",
        "fa-inbox",
        "reports:manager_school_tickets",
    ),
    (
        caps.DRAFT_CIRCULARS,
        "مسودات التعاميم",
        "إعداد المسودات وإرسالها للمدير للمراجعة والنشر",
        "fa-file-pen",
        "reports:circular_draft_list",
    ),
    (
        caps.VIEW_AUDIT_LOG,
        "سجل نطاقي",
        "قراءة سجل الإجراءات داخل اختصاصك فقط",
        "fa-clock-rotate-left",
        "reports:school_audit_logs",
    ),
    (
        caps.MANAGE_LAB,
        "متابعة المختبر",
        "مراجعة العهدة والتجارب دون تنفيذ عمل المحضّر نيابةً عنه",
        "fa-flask",
        "reports:lab_dashboard",
    ),
    # هاتان لا تُمنحان للموظف كنطاق دائم، لكن قد تصلان بتفويض مؤقت صريح.
    (
        caps.ASSIGN_TASKS,
        "إدارة التكليفات المفوّضة",
        "توزيع التكليفات خلال مدة التفويض",
        "fa-diagram-project",
        "reports:assignment_board",
    ),
    (
        caps.TRACK_PLANS,
        "متابعة الخطط المفوّضة",
        "متابعة مهام الخطط خلال مدة التفويض",
        "fa-compass-drafting",
        "reports:plan_list",
    ),
)


_SCOPE_DEPENDENT = {
    caps.VIEW_SCHOOL_DASHBOARD,
    caps.REVIEW_REPORTS,
    caps.VIEW_ACHIEVEMENTS,
    caps.HANDLE_REQUESTS,
    caps.VIEW_AUDIT_LOG,
    caps.ASSIGN_TASKS,
    caps.TRACK_PLANS,
    caps.MANAGE_LAB,
}


def _scope_for(membership):
    if membership is None:
        return None
    try:
        return membership.scope
    except StaffScope.DoesNotExist:
        return None


def _scope_names(scope, school) -> list[str]:
    if scope is None:
        return []
    return list(
        scope.departments.filter(school=school, is_active=True)
        .order_by("name")
        .values_list("name", flat=True)
    )


def _lab_scope_names(scope, user, school) -> list[str]:
    """نطاق المحضّر من شاشة الصلاحيات أو عضوية القسم في الإضافة السريعة."""
    names = set(_scope_names(scope, school))
    names.update(
        DepartmentMembership.objects.filter(
            teacher=user,
            department__school=school,
            department__is_active=True,
        ).values_list("department__name", flat=True)
    )
    return sorted(str(name) for name in names if name)


def _capability_labels(codes) -> list[str]:
    return [caps.BY_CODE[code].label for code in sorted(codes) if code in caps.BY_CODE]


def _actions(definitions, effective, permanent, delegated) -> list[dict]:
    return [
        {
            "code": code,
            "label": label,
            "hint": hint,
            "icon": icon,
            "url": reverse(url_name),
            "source": (
                "delegation" if code in delegated and code not in permanent else "scope"
            ),
        }
        for code, label, hint, icon, url_name in definitions
        if code in effective
    ]


def _admin_core_actions(effective) -> list[dict]:
    can_archive = caps.ARCHIVE_DOCUMENTS in effective
    can_organize = caps.MANAGE_MEETINGS in effective
    return [
        {
            "label": "الطلبات المسندة إليّ",
            "hint": "متابعة الطلبات الواردة وتسجيل الإجراء المطلوب",
            "icon": "fa-inbox",
            "url": reverse("reports:assigned_to_me"),
        },
        {
            "label": "تكليفاتي التنفيذية",
            "hint": "تحديث الإنجاز ورفع الشواهد ثم الإرسال للمراجعة",
            "icon": "fa-list-check",
            "url": reverse("reports:my_assignments"),
        },
        {
            "label": "أرشيف الوثائق",
            "hint": (
                "رفع الوثائق وتصنيفها ومراجعة ما ينتظر الأرشفة"
                if can_archive
                else "رفع وثائقي وتصنيفها وإرسالها لاعتماد الأرشفة"
            ),
            "icon": "fa-folder-tree",
            "url": reverse("reports:document_archive"),
        },
        {
            "label": "الاجتماعات والمحاضر",
            "hint": (
                "تنظيم الاجتماعات وكتابة المحاضر ومتابعة اعتمادها"
                if can_organize
                else "متابعة الاجتماعات التي دُعيت إليها ومحاضرها"
            ),
            "icon": "fa-users-rectangle",
            "url": reverse("reports:meeting_list"),
        },
        {
            "label": "توثيق عمل إداري",
            "hint": "تسجيل ما نُفّذ ونتائجه وشواهده في تقرير واضح",
            "icon": "fa-file-circle-plus",
            "url": reverse("reports:add_report"),
        },
        {
            "label": "المبادرات والممارسات",
            "hint": "اقتراح تحسين أو ممارسة ورفعها للمدير للاعتماد",
            "icon": "fa-lightbulb",
            "url": reverse("reports:initiative_list"),
        },
    ]


def _lab_technician_core_actions() -> list[dict]:
    """المهام المتكررة للمحضّر، مع إبقاء أدواته المهنية الشخصية."""
    return [
        {
            "label": "لوحة المختبر",
            "hint": "ملخص العهدة والتجارب والتنبيهات التي تحتاج متابعة",
            "icon": "fa-flask-vial",
            "url": reverse("reports:lab_dashboard"),
        },
        {
            "label": "إدارة عهدة المختبر",
            "hint": "إضافة الأصناف وتسجيل التسليم والإرجاع ومراجعة الجرد",
            "icon": "fa-boxes-stacked",
            "url": reverse("reports:lab_assets"),
        },
        {
            "label": "توثيق التجارب",
            "hint": "حفظ التجارب وإكمالها ثم إرسالها للاعتماد",
            "icon": "fa-microscope",
            "url": reverse("reports:lab_experiments"),
        },
        {
            "label": "تقاريري المهنية",
            "hint": "توثيق إنجازات العمل وشواهدها ومتابعة اعتمادها",
            "icon": "fa-file-circle-plus",
            "url": reverse("reports:add_report"),
        },
        {
            "label": "تكليفاتي",
            "hint": "متابعة ما أُسند إليّ وتحديث الإنجاز ورفع الشواهد",
            "icon": "fa-list-check",
            "url": reverse("reports:my_assignments"),
        },
        {
            "label": "الاجتماعات والمحاضر",
            "hint": "متابعة الاجتماعات التي دُعيت إليها ومحاضرها",
            "icon": "fa-users-rectangle",
            "url": reverse("reports:meeting_list"),
        },
    ]


def build_staff_workspaces(user, school) -> dict:
    if school is None or not getattr(user, "is_authenticated", False):
        return dict(EMPTY_STAFF_WORKSPACES)

    memberships = list(
        SchoolMembership.objects.filter(
            school=school,
            teacher=user,
            is_active=True,
        )
        .select_related("scope")
        .order_by("id")
    )
    if not memberships:
        return dict(EMPTY_STAFF_WORKSPACES)

    by_role = {membership.role_type: membership for membership in memberships}
    role_order = {
        SchoolMembership.RoleType.MANAGER: 0,
        SchoolMembership.RoleType.DEPUTY: 1,
        SchoolMembership.RoleType.ADMIN_STAFF: 2,
        SchoolMembership.RoleType.TEACHER: 3,
    }
    ordered = sorted(memberships, key=lambda item: role_order.get(item.role_type, 99))
    role_labels = [
        membership.get_job_title_display()
        if membership.job_title == SchoolMembership.JobTitle.LAB_TECH
        else membership.get_role_type_display()
        for membership in ordered
    ]

    deputy_membership = by_role.get(SchoolMembership.RoleType.DEPUTY)
    admin_membership = by_role.get(SchoolMembership.RoleType.ADMIN_STAFF)
    delegations = (
        active_delegations(user, school)
        if deputy_membership is not None or admin_membership is not None
        else []
    )
    delegated = set()
    for delegation in delegations:
        delegated |= delegation.capability_codes()

    result = {
        **EMPTY_STAFF_WORKSPACES,
        "active_school_role_labels": role_labels,
        "has_multiple_active_school_roles": len(role_labels) > 1,
        "has_teacher_role": SchoolMembership.RoleType.TEACHER in by_role,
    }

    if deputy_membership is not None:
        scope = _scope_for(deputy_membership)
        permanent = set(scope.capability_codes()) if scope is not None else set()
        effective = permanent | delegated
        scope_names = _scope_names(scope, school)
        template = caps.TEMPLATES_BY_CODE.get(
            (getattr(scope, "template_code", "") or "").strip()
        )
        result.update(
            {
                "is_deputy_workspace": True,
                "deputy_domain_label": (
                    scope.get_domain_display() if scope is not None and scope.domain else ""
                ),
                "deputy_template_label": template.label if template else "",
                "deputy_scope_names": scope_names,
                "deputy_capability_labels": _capability_labels(effective),
                "deputy_capability_count": len(effective),
                "deputy_workspace_actions": _actions(
                    _DEPUTY_ACTIONS, effective, permanent, delegated
                ),
                "deputy_needs_setup": not effective,
                "deputy_scope_missing": bool(effective & _SCOPE_DEPENDENT)
                and not scope_names,
                "deputy_has_temporary_delegation": bool(delegations),
            }
        )

    if admin_membership is not None:
        is_lab_technician = (
            admin_membership.job_title == SchoolMembership.JobTitle.LAB_TECH
        )
        scope = _scope_for(admin_membership)
        permanent = set(scope.capability_codes()) if scope is not None else set()
        # عند اجتماع دور الوكيل والموظف، يُنسب التفويض إلى مركز الوكيل حتى لا
        # تظهر الأداة ذاتها مرتين. الموظف المنفرد يستفيد من التفويض كالمعتاد.
        admin_delegated = delegated if deputy_membership is None else set()
        effective = permanent | admin_delegated
        scope_names = (
            _lab_scope_names(scope, user, school)
            if is_lab_technician
            else _scope_names(scope, school)
        )
        # أدوات المتابعة المرتبطة بنطاق لا تفيد بلا أقسام: تفتح شاشة فارغة
        # وتوحي للمحضّر أن عليه عملاً إشرافياً لم يُسند إليه. نحفظ الصلاحيات
        # كما هي، ونُظهر تنبيه الإعداد، لكن لا نعرض الأداة حتى يكتمل نطاقها.
        visible_effective = (
            effective - _SCOPE_DEPENDENT
            if is_lab_technician and not scope_names
            else effective
        )
        template = caps.TEMPLATES_BY_CODE.get(
            (getattr(scope, "template_code", "") or "").strip()
        )
        basic_template = caps.TEMPLATES_BY_CODE.get("admin_staff_basic")
        result.update(
            {
                "is_admin_staff_workspace": True,
                "is_lab_technician_workspace": is_lab_technician,
                "admin_staff_template_label": (
                    "محضّر المختبر"
                    if is_lab_technician
                    else template.label
                    if template is not None
                    else "صلاحيات إدارية مخصصة"
                    if effective
                    else basic_template.label
                    if basic_template is not None
                    else "المهام الأساسية"
                ),
                "admin_staff_scope_names": scope_names,
                "admin_staff_capability_labels": _capability_labels(visible_effective),
                "admin_staff_capability_count": len(visible_effective),
                "admin_staff_core_actions": (
                    _lab_technician_core_actions()
                    if is_lab_technician
                    else _admin_core_actions(effective)
                ),
                "admin_staff_workspace_actions": _actions(
                    _ADMIN_CAPABILITY_ACTIONS,
                    visible_effective,
                    permanent,
                    admin_delegated,
                ),
                "admin_staff_scope_missing": bool(effective & _SCOPE_DEPENDENT)
                and not scope_names,
                "admin_staff_has_temporary_delegation": bool(admin_delegated),
                "admin_staff_has_enhanced_permissions": bool(visible_effective),
            }
        )

    return result
