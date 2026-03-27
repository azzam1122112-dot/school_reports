# reports/permissions.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from functools import wraps
from typing import Iterable, Set, Any, Optional, List

from django.contrib import messages
from django.db.models import QuerySet, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect

from .models import Department, SchoolMembership, School

# نحاول الاستيراد المرن لعضويات الأقسام
try:
    from .models import DepartmentMembership  # type: ignore
except Exception:  # pragma: no cover
    DepartmentMembership = None  # type: ignore

__all__ = [
    "get_officer_departments",
    "get_officer_department",
    "get_member_departments",
    "is_officer",
    "is_department_member",
    "has_legacy_manager_role",
    "get_school_manager_school_ids",
    "is_school_manager",
    "is_report_viewer_for_school",
    "effective_user_role_label",
    "can_delete_report",
    "can_edit_report",
    "can_share_report",
    "is_platform_admin",
    "platform_allowed_schools_qs",
    "platform_can_access_school",
    "role_required",
    "allowed_categories_for",
    "restrict_queryset_for_user",
]


def is_platform_admin(user) -> bool:
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "is_platform_admin", False))


def _resolved_school_id(
    *,
    active_school: Optional[School] = None,
    active_school_id: Optional[int] = None,
) -> Optional[int]:
    try:
        if active_school_id:
            return int(active_school_id)
    except Exception:
        pass
    try:
        if active_school is not None:
            return int(getattr(active_school, "id", None) or 0) or None
    except Exception:
        pass
    return None


def _school_role_labels(active_school: Optional[School]) -> dict[str, str]:
    gender = (getattr(active_school, "gender", "") or "").strip().lower()
    girls_value = str(getattr(getattr(School, "Gender", None), "GIRLS", "girls")).strip().lower()
    is_girls = gender == girls_value
    return {
        "manager": "مديرة المدرسة" if is_girls else "مدير المدرسة",
        "teacher": "المعلمة" if is_girls else "المعلم",
        "admin_staff": "موظفة إدارية" if is_girls else "موظف إداري",
        "lab_tech": "محضرة مختبر" if is_girls else "محضر مختبر",
    }


def is_report_viewer_for_school(
    user,
    active_school: Optional[School] = None,
    *,
    active_school_id: Optional[int] = None,
) -> bool:
    """Single source of truth for the read-only report viewer role.

    - When ``active_school``/``active_school_id`` is provided, the check is scoped to that school.
    - When omitted, any active report-viewer membership counts.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
        return False

    school_id = _resolved_school_id(active_school=active_school, active_school_id=active_school_id)

    try:
        cache = getattr(user, "_report_viewer_membership_cache", None)
        if not isinstance(cache, dict):
            cache = {}
            setattr(user, "_report_viewer_membership_cache", cache)

        cache_key = int(school_id or 0)
        if cache_key in cache:
            return bool(cache[cache_key])

        qs = SchoolMembership.objects.filter(
            teacher=user,
            role_type=SchoolMembership.RoleType.REPORT_VIEWER,
            is_active=True,
        )
        if school_id:
            qs = qs.filter(school_id=school_id)

        result = qs.exists()
        cache[cache_key] = bool(result)
        return bool(result)
    except Exception:
        return False


def platform_allowed_schools_qs(user) -> QuerySet[School]:
    """Schools accessible to a platform admin (scope-based)."""
    if not getattr(user, "is_authenticated", False):
        return School.objects.none()

    if getattr(user, "is_superuser", False):
        return School.objects.filter(is_active=True)

    if not is_platform_admin(user):
        return School.objects.none()

    qs = School.objects.filter(is_active=True)
    scope = getattr(user, "platform_scope", None)
    # Defense-in-depth: if scope is missing, do NOT grant broad access.
    # A scope row should exist for every platform admin (created in admin flows).
    if scope is None:
        return School.objects.none()

    try:
        if scope.allowed_schools.exists():
            return qs.filter(id__in=scope.allowed_schools.values_list("id", flat=True))
    except Exception:
        pass

    try:
        gs = (getattr(scope, "gender_scope", None) or "all").strip().lower()
        if gs in {"boys", "girls"}:
            qs = qs.filter(gender=gs)
    except Exception:
        pass

    try:
        cities = getattr(scope, "allowed_cities", None) or []
        cities = [str(c).strip() for c in cities if str(c).strip()]
        if cities:
            qs = qs.filter(city__in=cities)
    except Exception:
        pass

    return qs


def platform_can_access_school(user, school: School | None) -> bool:
    if school is None:
        return False
    try:
        return platform_allowed_schools_qs(user).filter(id=school.id).exists()
    except Exception:
        return False


# ==============================
# أدوات داخلية
# ==============================
def _user_role(user):
    """يعيد كائن Role المرتبط بالمستخدم إن وجد، وإلا None."""
    try:
        return getattr(user, "role", None)
    except Exception:
        return None


def _user_role_slug(user) -> Optional[str]:
    """يعيد slug للدور الحالي للمستخدم أو None إن لم يوجد."""
    role = _user_role(user)
    return getattr(role, "slug", None) if role else None


def has_legacy_manager_role(user) -> bool:
    """توافق خلفي: هل ما زال الحساب يعتمد على Role.slug='manager'؟"""
    slug = (_user_role_slug(user) or "").strip().lower()
    return slug == str(SchoolMembership.RoleType.MANAGER).strip().lower()


def _school_membership_cache(user) -> dict:
    cache = getattr(user, "_school_membership_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(user, "_school_membership_cache", cache)
    return cache


def _get_school_membership(
    user,
    *,
    active_school: Optional[School] = None,
    active_school_id: Optional[int] = None,
    role_types: Optional[Iterable[str]] = None,
) -> Optional[SchoolMembership]:
    """Fetch one active school membership and memoize it on the user object."""
    if not getattr(user, "is_authenticated", False):
        return None

    school_id = _resolved_school_id(active_school=active_school, active_school_id=active_school_id)
    if not school_id:
        return None

    normalized_role_types = tuple(sorted({str(v).strip().lower() for v in (role_types or []) if str(v).strip()}))
    cache_key = (int(school_id), normalized_role_types)
    cache = _school_membership_cache(user)
    if cache_key in cache:
        return cache[cache_key]

    try:
        qs = (
            SchoolMembership.objects.select_related("school")
            .filter(teacher=user, school_id=school_id, is_active=True)
            .order_by("id")
        )
        if normalized_role_types:
            qs = qs.filter(role_type__in=list(normalized_role_types))
        membership = qs.first()
    except Exception:
        membership = None

    cache[cache_key] = membership
    return membership


def get_school_manager_school_ids(user, *, allow_legacy_role: bool = False) -> Set[int]:
    """Returns school ids where the user should be treated as a school manager.

    - `allow_legacy_role=False`: strict modern source of truth via `SchoolMembership`.
    - `allow_legacy_role=True`: compatibility mode for old `Role.slug='manager'` users,
      scoped only to schools where they still have an active membership.
    """
    if not getattr(user, "is_authenticated", False):
        return set()

    cache = getattr(user, "_school_manager_ids_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(user, "_school_manager_ids_cache", cache)

    cache_key = bool(allow_legacy_role)
    if cache_key in cache:
        return set(cache[cache_key])

    try:
        qs = SchoolMembership.objects.filter(teacher=user, is_active=True)
        if allow_legacy_role and has_legacy_manager_role(user):
            ids = {int(x) for x in qs.values_list("school_id", flat=True) if x}
        else:
            ids = {
                int(x)
                for x in qs.filter(role_type=SchoolMembership.RoleType.MANAGER).values_list("school_id", flat=True)
                if x
            }
    except Exception:
        ids = set()

    cache[cache_key] = tuple(sorted(ids))
    return ids


def is_school_manager(
    user,
    active_school: Optional[School] = None,
    *,
    active_school_id: Optional[int] = None,
    allow_legacy_role: bool = False,
) -> bool:
    """Canonical manager detection.

    Use strict mode (`allow_legacy_role=False`) for authorization.
    Use compatibility mode (`allow_legacy_role=True`) only for display/login bridges
    while the legacy `Role` model is still present.
    """
    if not getattr(user, "is_authenticated", False):
        return False

    school_id = _resolved_school_id(active_school=active_school, active_school_id=active_school_id)
    if school_id:
        if _get_school_membership(
            user,
            active_school=active_school,
            active_school_id=school_id,
            role_types=[SchoolMembership.RoleType.MANAGER],
        ) is not None:
            return True
        if allow_legacy_role and has_legacy_manager_role(user):
            return _get_school_membership(
                user,
                active_school=active_school,
                active_school_id=school_id,
            ) is not None
        return False

    return bool(get_school_manager_school_ids(user, allow_legacy_role=allow_legacy_role))


def effective_user_role_label(
    user,
    active_school: Optional[School] = None,
    *,
    active_school_id: Optional[int] = None,
) -> str:
    """Single source of truth for the role label shown in the UI."""
    if user is None:
        return "مستخدم"

    school_id = _resolved_school_id(active_school=active_school, active_school_id=active_school_id)
    school = active_school
    if school is None and school_id:
        try:
            school = School.objects.filter(pk=school_id, is_active=True).only("id", "gender").first()
        except Exception:
            school = None

    cache = getattr(user, "_effective_role_label_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        setattr(user, "_effective_role_label_cache", cache)

    cache_key = int(school_id or 0)
    if cache_key in cache:
        return str(cache[cache_key] or "مستخدم")

    labels = _school_role_labels(school)
    label = "مستخدم"
    try:
        if getattr(user, "is_superuser", False):
            label = "مدير النظام"
        elif is_platform_admin(user):
            scope = getattr(user, "platform_scope", None)
            role_obj = getattr(scope, "role", None) if scope is not None else None
            label = (getattr(role_obj, "name", "") or "").strip() or "المشرف العام"
        elif is_school_manager(
            user,
            active_school=school,
            active_school_id=school_id,
            allow_legacy_role=True,
        ):
            label = labels["manager"]
        elif getattr(user, "is_staff", False):
            label = "مدير النظام"
        else:
            teacher_membership = _get_school_membership(
                user,
                active_school=school,
                active_school_id=school_id,
                role_types=[SchoolMembership.RoleType.TEACHER],
            )
            if teacher_membership is not None:
                job_title = (getattr(teacher_membership, "job_title", "") or "").strip()
                if job_title == SchoolMembership.JobTitle.ADMIN_STAFF:
                    label = labels["admin_staff"]
                elif job_title == SchoolMembership.JobTitle.LAB_TECH:
                    label = labels["lab_tech"]
                else:
                    label = labels["teacher"]
            elif is_report_viewer_for_school(user, active_school=school, active_school_id=school_id):
                label = str(SchoolMembership.RoleType.REPORT_VIEWER.label)
            else:
                role_obj = _user_role(user)
                label = (
                    (getattr(role_obj, "name", None) or "").strip()
                    or (getattr(role_obj, "slug", None) or "").strip()
                    or "مستخدم"
                )
    except Exception:
        label = "مستخدم"

    cache[cache_key] = label
    return label


# ==============================
# اكتشاف “مسؤول قسم” (متعدد الأقسام)
# ==============================
def get_officer_departments(user, *, active_school: Optional[School] = None) -> List[Department]:
    """
        يعيد قائمة الأقسام التي المستخدم مسؤول عنها عبر DepartmentMembership.role_type = OFFICER.

        ملاحظة: لا نعتمد على user.role/Role.slug لأن الأقسام الآن مخصصة لكل مدرسة ويمكن أن تتكرر slugs.
    تُعاد قائمة بدون تكرار ومحافظة على الترتيب.
    """
    if not getattr(user, "is_authenticated", False):
        return []

    seen = set()
    results: List[Department] = []

    # عبر العضويات
    if DepartmentMembership is not None:
        try:
            memb_qs = (
                DepartmentMembership.objects.select_related("department")
                .filter(teacher=user, role_type=getattr(DepartmentMembership, "OFFICER", "officer"),
                        department__is_active=True)
            )
            if active_school is not None:
                memb_qs = memb_qs.filter(department__school=active_school)
            for m in memb_qs:
                d = m.department
                if d and d.pk not in seen:
                    seen.add(d.pk)
                    results.append(d)
        except Exception:
            pass

    return results


def get_officer_department(user) -> Optional[Department]:
    """توافق خلفي: أول قسم من get_officer_departments أو None."""
    depts = get_officer_departments(user)
    return depts[0] if depts else None


def get_member_departments(user, *, active_school: Optional[School] = None) -> List[Department]:
    """
        يعيد قائمة الأقسام التي المستخدم عضو فيها (TEACHER) عبر DepartmentMembership.
        
        هؤلاء الأعضاء يحصلون على صلاحيات قراءة فقط (عرض + طباعة) لتقارير قسمهم،
        دون صلاحيات المشاركة أو الحذف (على عكس رؤساء الأقسام OFFICER).
    """
    if not getattr(user, "is_authenticated", False):
        return []

    seen = set()
    results: List[Department] = []

    if DepartmentMembership is not None:
        try:
            memb_qs = (
                DepartmentMembership.objects.select_related("department")
                .filter(teacher=user, role_type=getattr(DepartmentMembership, "TEACHER", "teacher"),
                        department__is_active=True)
            )
            if active_school is not None:
                memb_qs = memb_qs.filter(department__school=active_school)
            for m in memb_qs:
                d = m.department
                if d and d.pk not in seen:
                    seen.add(d.pk)
                    results.append(d)
        except Exception:
            pass

    return results


def is_officer(user) -> bool:
    """هل المستخدم مسؤول قسم؟ (عضويات أو مطابقة الدور)"""
    return bool(get_officer_departments(user))


def is_department_member(user, *, active_school: Optional[School] = None) -> bool:
    """هل المستخدم عضو في أي قسم (TEACHER)؟"""
    return bool(get_member_departments(user, active_school=active_school))


def _scope_school_id(*, active_school: Optional[School] = None, report_school: Optional[School] = None) -> Optional[int]:
    try:
        if active_school is not None:
            return int(getattr(active_school, "id", None) or 0) or None
    except Exception:
        pass
    try:
        if report_school is not None:
            return int(getattr(report_school, "id", None) or 0) or None
    except Exception:
        pass
    return None


def _build_report_permission_scope(user, *, school_id: Optional[int]) -> dict:
    """Build a lightweight permission scope once per user/school context.

    This avoids repeating manager/officer DB lookups for every report row.
    """
    scope = {
        "is_authenticated": bool(getattr(user, "is_authenticated", False)),
        "is_superuser": bool(getattr(user, "is_superuser", False)),
        "is_platform_admin": bool(is_platform_admin(user)),
        "manager_school_ids": set(),
        "officer_reporttype_ids": set(),
    }

    if not scope["is_authenticated"] or scope["is_superuser"] or scope["is_platform_admin"]:
        return scope

    manager_ids = get_school_manager_school_ids(user)
    if school_id is not None:
        manager_ids = {sid for sid in manager_ids if sid == school_id}
    scope["manager_school_ids"] = set(manager_ids)

    try:
        if DepartmentMembership is None:
            return scope
        officer_value = getattr(DepartmentMembership, "OFFICER", "officer")
        depts_qs = Department.objects.filter(
            is_active=True,
            memberships__teacher=user,
            memberships__role_type=officer_value,
        )
        if school_id is not None:
            depts_qs = depts_qs.filter(school_id=school_id)
        rt_ids = set(depts_qs.values_list("reporttypes__id", flat=True))
        scope["officer_reporttype_ids"] = {int(x) for x in rt_ids if x}
    except Exception:
        scope["officer_reporttype_ids"] = set()

    return scope


def _get_report_permission_scope(user, *, active_school: Optional[School] = None, report_school: Optional[School] = None) -> dict:
    sid = _scope_school_id(active_school=active_school, report_school=report_school)
    cache_obj = getattr(user, "_report_perm_scope_cache", None)
    if not isinstance(cache_obj, dict):
        cache_obj = {}
        setattr(user, "_report_perm_scope_cache", cache_obj)

    key = sid if sid is not None else "all"
    if key not in cache_obj:
        cache_obj[key] = _build_report_permission_scope(user, school_id=sid)
    return cache_obj[key]


def _can_manage_report(user, report, *, active_school: Optional[School] = None) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False

    report_teacher_id = getattr(report, "teacher_id", None)
    report_school = getattr(report, "school", None)
    report_school_id = getattr(report, "school_id", None)
    report_category_id = getattr(report, "category_id", None)

    scope = _get_report_permission_scope(user, active_school=active_school, report_school=report_school)
    if scope.get("is_superuser"):
        return True
    if scope.get("is_platform_admin"):
        return False

    if report_teacher_id == getattr(user, "id", None):
        return True

    if report_school_id in scope.get("manager_school_ids", set()):
        return True

    if report_category_id in scope.get("officer_reporttype_ids", set()):
        return True

    return False


def can_delete_report(user, report, *, active_school: Optional[School] = None) -> bool:
    """
    يحدد هل المستخدم يستطيع حذف التقرير.
    
    الصلاحيات:
    - السوبر: نعم
    - مشرف المنصة: لا (عرض فقط)
    - مدير المدرسة: نعم
    - رئيس القسم (OFFICER): نعم (للتقارير المرتبطة بقسمه)
    - عضو القسم (TEACHER): لا (عرض فقط)
    - صاحب التقرير: نعم
    """
    return _can_manage_report(user, report, active_school=active_school)


def can_share_report(user, report, *, active_school: Optional[School] = None) -> bool:
    """
    يحدد هل المستخدم يستطيع مشاركة التقرير (إنشاء رابط عام).
    
    الصلاحيات:
    - السوبر: نعم
    - مشرف المنصة: لا (عرض فقط)
    - مدير المدرسة: نعم
    - رئيس القسم (OFFICER): نعم (للتقارير المرتبطة بقسمه)
    - عضو القسم (TEACHER): لا (عرض فقط)
    - صاحب التقرير: نعم
    """
    return _can_manage_report(user, report, active_school=active_school)


def can_edit_report(user, report, *, active_school: Optional[School] = None) -> bool:
    """
    يحدد هل المستخدم يستطيع تعديل التقرير.
    
    الصلاحيات:
    - السوبر: نعم
    - مشرف المنصة: لا (عرض فقط)
    - مدير المدرسة: نعم
    - رئيس القسم (OFFICER): نعم (للتقارير المرتبطة بقسمه)
    - عضو القسم (TEACHER): لا (عرض فقط)
    - صاحب التقرير: نعم
    """
    return _can_manage_report(user, report, active_school=active_school)


# ==============================
# ديكوريتر حصر الوصول حسب الدور (بالـ slug)
# ==============================
def role_required(allowed_roles: Iterable[str]):
    """
    مثال:
        @login_required(login_url="reports:login")
        @role_required({"manager"})
        def some_view(...): ...
    - السوبر يمر دائمًا.
    - المقارنة تتم بالـ slug للدور.
    """
    allowed = set(allowed_roles or [])

    def _has_active_schools() -> bool:
        try:
            return School.objects.filter(is_active=True).exists()
        except Exception:
            return False

    def _get_active_school_id(request: HttpRequest) -> int | None:
        try:
            sid = request.session.get("active_school_id")
            return int(sid) if sid else None
        except Exception:
            return None

    def decorator(view_func):
        @wraps(view_func)
        def _wrapped(request: HttpRequest, *args, **kwargs) -> HttpResponse:
            user = request.user
            if not getattr(user, "is_authenticated", False):
                return redirect("reports:login")

            # السوبر دومًا مسموح
            if getattr(user, "is_superuser", False):
                return view_func(request, *args, **kwargs)

            active_school_id = _get_active_school_id(request)

            # إذا كان النظام متعدد المدارس ونتعامل مع صلاحيات مدرسية، نجبر اختيار مدرسة نشطة.
            if _has_active_schools() and not active_school_id:
                # نُجبر ذلك خصوصًا للصفحات المدرسية (manager)
                if "manager" in allowed:
                    # إن لم يكن المستخدم مدير مدرسة فعلي (عضوية)، لا نرسله لاختيار مدرسة.
                    # هذا يمنع تسريب صلاحية عبر Role.slug='manager' فقط.
                    if is_school_manager(user):
                        messages.error(request, "فضلاً اختر مدرسة أولاً.")
                        return redirect("reports:select_school")
                    messages.error(request, "لا تملك صلاحية الوصول إلى هذه الصفحة.")
                    return redirect("reports:home")

            role_slug = _user_role_slug(user)

            # ⚠️ المدير (manager) صلاحية مدرسية، لا نعتمد فقط على Role.slug
            if "manager" in allowed:
                if is_school_manager(user, active_school_id=active_school_id):
                    return view_func(request, *args, **kwargs)
            else:
                # أدوار عالمية أخرى يمكن السماح بها عبر Role.slug
                if role_slug in allowed:
                    return view_func(request, *args, **kwargs)

            # ملاحظة: دعم المدير تم أعلاه بشكل صريح ومشدّد

            messages.error(request, "لا تملك صلاحية الوصول إلى هذه الصفحة.")
            return redirect("reports:home")

        return _wrapped

    return decorator


# ==============================
# صلاحيات أنواع التقارير (بالاعتماد على الدور + أقسام المسؤول)
# ==============================
def allowed_categories_for(user, active_school: Optional[School] = None) -> Set[str]:
    """
        يعيد مجموعة أكواد ReportType المسموحة للمستخدم داخل مدرسة محددة:
            - {"all"} للسوبر.
            - مدير المدرسة (SchoolMembership.role_type=manager) داخل active_school: {"all"}.
            - رئيس قسم (OFFICER): أكواد reporttypes للأقسام التي هو مسؤول عنها.
            - عضو قسم (TEACHER): أكواد reporttypes للأقسام التي هو عضو فيها.

        ملاحظة: لا نستخدم Role.allowed_reporttypes هنا لأن Role عالمي وقد يخلط بين المدارس.
    """
    try:
        # سوبر/مشرف عام: يرى الكل (لكن عزل المدارس يُطبّق في الـ views)
        if getattr(user, "is_superuser", False):
            return {"all"}
        if is_platform_admin(user):
            return {"all"}
        if active_school is not None and SchoolMembership is not None:
            if is_school_manager(user, active_school=active_school):
                return {"all"}
            try:
                if is_report_viewer_for_school(user, active_school):
                    return {"all"}
            except Exception:
                pass

        if active_school is None:
            # بدون مدرسة نشطة لا نسمح بتوسيع الوصول
            return set()

        allowed_codes: Set[str] = set()
        
        # ✅ رؤساء الأقسام (OFFICER)
        try:
            officer_depts = get_officer_departments(user, active_school=active_school)
            for d in officer_depts:
                allowed_codes |= set(c for c in d.reporttypes.values_list("code", flat=True) if c)
        except Exception:
            pass

        # ✅ أعضاء الأقسام (TEACHER) - عرض فقط
        try:
            member_depts = get_member_departments(user, active_school=active_school)
            for d in member_depts:
                allowed_codes |= set(c for c in d.reporttypes.values_list("code", flat=True) if c)
        except Exception:
            pass

        return allowed_codes
    except Exception:
        return set()


# ==============================
# تقييد QuerySet بحسب المستخدم
# ==============================
def restrict_queryset_for_user(qs: QuerySet[Any], user, active_school: Optional[School] = None) -> QuerySet[Any]:
    """
    يقيّد QuerySet للتقارير بحسب صلاحيات المستخدم:
      - السوبر/المدير/الدور الذي يرى الكل: يرى الجميع.
      - غير ذلك: يرى تقاريره + أي تقرير يقع ضمن الأنواع المسموح بها له (من الدور/الأقسام).
    """
    # سوبر: لا قيود
    if getattr(user, "is_superuser", False):
        return qs

    # مشرف عام: رؤية مقيدة حسب المدارس المسموحة فقط (إن كان للموديل حقل school)
    if is_platform_admin(user):
        try:
            # إن كانت هناك مدرسة نشطة، نتأكد أنها ضمن المسموح
            if active_school is not None and not platform_can_access_school(user, active_school):
                return qs.none()
            if hasattr(qs.model, "school"):
                allowed_ids = list(platform_allowed_schools_qs(user).values_list("id", flat=True))
                return qs.filter(school_id__in=allowed_ids)
        except Exception:
            return qs.none()
        return qs

    # ✅ مدير المدرسة داخل active_school يرى كل التقارير (مع مراعاة فلترة المدرسة في الـ View)
    if active_school is not None and SchoolMembership is not None:
        try:
            if SchoolMembership.objects.filter(
                teacher=user,
                school=active_school,
                role_type__in=[
                    SchoolMembership.RoleType.MANAGER,
                ],
                is_active=True,
            ).exists():
                return qs
        except Exception:
            pass

    allowed_codes = allowed_categories_for(user, active_school)
    if "all" in allowed_codes:
        return qs

    conditions = Q(teacher=user)  # دائمًا يرى تقاريره
    if allowed_codes:
        conditions |= Q(category__code__in=list(allowed_codes))

    return qs.filter(conditions).distinct()
