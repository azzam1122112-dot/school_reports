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
    "can_delete_report",
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
    if not getattr(user, "is_authenticated", False):
        return False
    
    # السوبر: نعم
    if getattr(user, "is_superuser", False):
        return True
    
    # مشرف المنصة: لا
    if is_platform_admin(user):
        return False
    
    # صاحب التقرير: نعم
    if getattr(report, "teacher_id", None) == getattr(user, "id", None):
        return True
    
    # مدير المدرسة: نعم
    report_school = getattr(report, "school", None)
    if report_school and SchoolMembership is not None:
        try:
            if SchoolMembership.objects.filter(
                teacher=user,
                school=report_school,
                role_type=SchoolMembership.RoleType.MANAGER,
                is_active=True,
            ).exists():
                return True
        except Exception:
            pass
    
    # رئيس القسم: نعم (إذا كان التقرير ضمن قسمه)
    try:
        report_category = getattr(report, "category", None)
        if report_category:
            officer_depts = get_officer_departments(user, active_school=active_school or report_school)
            for dept in officer_depts:
                if dept.reporttypes.filter(pk=report_category.pk).exists():
                    return True
    except Exception:
        pass
    
    # الباقي: لا
    return False


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
    if not getattr(user, "is_authenticated", False):
        return False
    
    # السوبر: نعم
    if getattr(user, "is_superuser", False):
        return True
    
    # مشرف المنصة: لا
    if is_platform_admin(user):
        return False
    
    # صاحب التقرير: نعم
    if getattr(report, "teacher_id", None) == getattr(user, "id", None):
        return True
    
    # مدير المدرسة: نعم
    report_school = getattr(report, "school", None)
    if report_school and SchoolMembership is not None:
        try:
            if SchoolMembership.objects.filter(
                teacher=user,
                school=report_school,
                role_type=SchoolMembership.RoleType.MANAGER,
                is_active=True,
            ).exists():
                return True
        except Exception:
            pass
    
    # رئيس القسم: نعم (إذا كان التقرير ضمن قسمه)
    try:
        report_category = getattr(report, "category", None)
        if report_category:
            officer_depts = get_officer_departments(user, active_school=active_school or report_school)
            for dept in officer_depts:
                if dept.reporttypes.filter(pk=report_category.pk).exists():
                    return True
    except Exception:
        pass
    
    # الباقي: لا
    return False


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

    def _is_school_manager(user, *, school_id: int | None) -> bool:
        if SchoolMembership is None:
            return False
        try:
            filters = {
                "teacher": user,
                "role_type": SchoolMembership.RoleType.MANAGER,
                "is_active": True,
            }
            if school_id:
                filters["school_id"] = school_id
            return SchoolMembership.objects.filter(**filters).exists()
        except Exception:
            return False

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
                    messages.error(request, "فضلاً اختر مدرسة أولاً.")
                    return redirect("reports:select_school")

            role_slug = _user_role_slug(user)

            # ⚠️ المدير (manager) صلاحية مدرسية، لا نعتمد فقط على Role.slug
            if "manager" in allowed:
                if _is_school_manager(user, school_id=active_school_id):
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
            try:
                if SchoolMembership.objects.filter(
                    teacher=user,
                    school=active_school,
                    role_type__in=[
                        SchoolMembership.RoleType.MANAGER,
                    ],
                    is_active=True,
                ).exists():
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
