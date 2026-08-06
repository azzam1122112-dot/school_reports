# -*- coding: utf-8 -*-
"""شاشة «الأدوار والصلاحيات» لمدير المدرسة.

ما تحلّه هذه الشاشة: كان المدير يملك دورين اثنين (مدير/معلّم) ومسمّى وظيفياً
لا أثر له، فلم يكن أمامه سبيل لتمثيل وكيل أو موظف إداري إلا أن يعامل الجميع
معلمين. وهنا يوزّع الأدوار، ويحدّد نطاق كل وكيل، ويفوّض مؤقتاً عند غيابه.

الحدود التي تفرضها الشاشة على المدير نفسه:
- لا يُسند دور «مدير» — نقل الإدارة قرار خارج المدرسة.
- لا يمنح صلاحية غير معرَّفة في مرجع الكود.
- لا يفوّض من ليس منسوباً في مدرسته.
وكلها منفَّذة في النماذج لا في القالب.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .. import capabilities as caps
from ..forms_staff_roles import DelegationForm, StaffRoleAssignForm, StaffScopeForm
from ..models import Delegation, SchoolMembership, StaffScope
from ..permissions import prefetch_memberships_for_school, role_required
from ._helpers import *  # noqa: F401,F403 — نفس مدخلات بقية شاشات المدير
from ._helpers import _get_active_school, _user_manager_schools

__all__ = [
    "staff_roles",
    "staff_role_scope",
    "delegation_revoke",
]


def _require_manager_school(request):
    """المدرسة النشطة التي يديرها الطالب — أو إعادة توجيه."""
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return None, redirect("reports:select_school")
    if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
        messages.error(request, "ليست لديك صلاحية كمدير على هذه المدرسة.")
        return None, redirect("reports:select_school")
    return active_school, None


def _roster(school) -> list[dict]:
    """منسوبو المدرسة، صفٌّ لكل شخص لا لكل عضوية.

    الشاشة تعرض أشخاصاً لا عضويات: صاحب الدورين سطر واحد يحمل وسمَين، لا
    سطران يوهمان بأنهما رجلان.
    """
    memberships = (
        SchoolMembership.objects.filter(
            school=school,
            is_active=True,
            role_type__in=SchoolMembership.STAFF_ROLES,
        )
        .select_related("teacher")
        .prefetch_related("scope__departments")
        .order_by("teacher__name", "id")
    )

    by_person: dict[int, dict] = {}
    for membership in memberships:
        row = by_person.setdefault(
            membership.teacher_id,
            {
                "person": membership.teacher,
                "memberships": [],
                "roles": [],
                "primary": None,
                "scope": None,
            },
        )
        row["memberships"].append(membership)
        row["roles"].append(membership.role_type)

    # الدور «الرئيسي» هو الأعلى إشرافاً — فهو ما يُعرض وسماً أولَ وما يُضبط نطاقه.
    precedence = {
        SchoolMembership.RoleType.DEPUTY: 0,
        SchoolMembership.RoleType.ADMIN_STAFF: 1,
        SchoolMembership.RoleType.TEACHER: 2,
    }
    rows = []
    for row in by_person.values():
        primary = min(row["memberships"], key=lambda m: precedence.get(m.role_type, 9))
        row["primary"] = primary
        row["scope"] = getattr(primary, "scope", None)
        row["can_have_scope"] = primary.role_type in {
            SchoolMembership.RoleType.DEPUTY,
            SchoolMembership.RoleType.ADMIN_STAFF,
        }
        row["also_teaches"] = (
            SchoolMembership.RoleType.TEACHER in row["roles"]
            and primary.role_type != SchoolMembership.RoleType.TEACHER
        )
        rows.append(row)

    rows.sort(
        key=lambda r: (
            precedence.get(r["primary"].role_type, 9),
            (getattr(r["person"], "name", "") or ""),
        )
    )
    prefetch_memberships_for_school([r["person"] for r in rows], school)
    return rows


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def staff_roles(request):
    """الشاشة الرئيسية: كشف الأدوار + الإسناد + التفويضات."""
    active_school, redirect_response = _require_manager_school(request)
    if redirect_response is not None:
        return redirect_response

    assign_form = StaffRoleAssignForm(school=active_school)
    delegation_form = DelegationForm(school=active_school, delegator=request.user)

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip()

        if action == "assign_role":
            assign_form = StaffRoleAssignForm(request.POST, school=active_school)
            if assign_form.is_valid():
                with transaction.atomic():
                    membership = assign_form.apply(actor=request.user)
                messages.success(
                    request,
                    f"تم إسناد دور «{membership.get_role_type_display()}» إلى {membership.teacher.name}.",
                )
                return redirect("reports:staff_roles")
            messages.error(request, "تعذّر إسناد الدور — تحقّق من الحقول.")

        elif action == "grant_delegation":
            delegation_form = DelegationForm(
                request.POST, school=active_school, delegator=request.user
            )
            if delegation_form.is_valid():
                delegation = delegation_form.save(commit=False)
                delegation.school = active_school
                delegation.delegator = request.user
                try:
                    delegation.full_clean()
                except Exception as exc:  # عرض رسائل التحقق النموذجية كما هي
                    messages.error(request, getattr(exc, "messages", ["تعذّر منح التفويض."])[0])
                else:
                    delegation.save()
                    messages.success(
                        request,
                        f"تم تفويض {delegation.delegate.name} حتى "
                        f"{timezone.localtime(delegation.ends_at):%Y-%m-%d %H:%M}.",
                    )
                    return redirect("reports:staff_roles")
            else:
                messages.error(request, "تعذّر منح التفويض — تحقّق من الحقول.")

        else:
            messages.error(request, "إجراء غير معروف.")
            return redirect("reports:staff_roles")

    delegations = list(
        Delegation.objects.filter(school=active_school)
        .select_related("delegate", "delegator")
        .order_by("-starts_at", "-id")[:25]
    )

    return render(
        request,
        "reports/staff_roles.html",
        {
            "active": "staff_roles",
            "active_school": active_school,
            "rows": _roster(active_school),
            "assign_form": assign_form,
            "delegation_form": delegation_form,
            "delegations": delegations,
            "active_delegation_count": sum(1 for d in delegations if d.state == "active"),
            "role_help": {
                SchoolMembership.RoleType.DEPUTY: "إشراف ومراجعة ضمن نطاق يحدّده المدير. لا يعتمد نهائياً.",
                SchoolMembership.RoleType.ADMIN_STAFF: "إعداد وتنفيذ وتوثيق. يرفع عمله للمراجعة.",
                SchoolMembership.RoleType.TEACHER: "توثيق أعماله المهنية وملف إنجازه وتنفيذ التكليفات.",
            },
        },
    )


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def staff_role_scope(request, pk: int):
    """ضبط نطاق عضوية بعينها."""
    active_school, redirect_response = _require_manager_school(request)
    if redirect_response is not None:
        return redirect_response

    membership = get_object_or_404(
        SchoolMembership.objects.select_related("teacher", "school"),
        pk=pk,
        school=active_school,
        is_active=True,
    )
    if membership.role_type not in {
        SchoolMembership.RoleType.DEPUTY,
        SchoolMembership.RoleType.ADMIN_STAFF,
    }:
        messages.error(request, "النطاق يُضبط للوكيل أو الموظف الإداري فقط.")
        return redirect("reports:staff_roles")

    scope = getattr(membership, "scope", None)
    if scope is None:
        scope = StaffScope(membership=membership)

    if request.method == "POST":
        form = StaffScopeForm(
            request.POST,
            instance=scope,
            school=active_school,
            role_type=membership.role_type,
        )
        template_code = (request.POST.get("template_code") or "").strip()
        apply_template = (request.POST.get("action") or "") == "apply_template"

        if apply_template and template_code:
            # تطبيق القالب لا يحفظ: يملأ الخانات ويترك القرار للمدير، فلا
            # يُفاجأ بحفظٍ لم يطلبه.
            template = caps.TEMPLATES_BY_CODE.get(template_code)
            if template is not None and template.role == membership.role_type:
                form = StaffScopeForm(
                    instance=scope,
                    school=active_school,
                    role_type=membership.role_type,
                    initial={
                        "capabilities": list(template.capabilities),
                        "template_code": template.code,
                        "domain": scope.domain,
                        "departments": scope.departments.all() if scope.pk else [],
                    },
                )
                messages.info(request, f"طُبِّق قالب «{template.label}». راجعه ثم احفظ.")
            else:
                messages.error(request, "قالب غير معتمد لهذا الدور.")
        elif form.is_valid():
            scope = form.save(commit=False)
            scope.membership = membership
            if scope.granted_by_id is None:
                scope.granted_by = request.user
            scope.save()
            form.save_m2m()
            messages.success(request, f"تم حفظ نطاق {membership.teacher.name}.")
            return redirect("reports:staff_roles")
        else:
            messages.error(request, "تعذّر حفظ النطاق — تحقّق من الحقول.")
    else:
        form = StaffScopeForm(
            instance=scope,
            school=active_school,
            role_type=membership.role_type,
        )

    return render(
        request,
        "reports/staff_role_scope.html",
        {
            "active": "staff_roles",
            "active_school": active_school,
            "membership": membership,
            "scope": scope,
            "form": form,
            "templates": caps.templates_for_role(membership.role_type),
            "capability_groups": caps.grouped_for_role(membership.role_type),
            "capability_meta": caps.BY_CODE,
        },
    )


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def delegation_revoke(request, pk: int):
    """سحب تفويض قائم. لا يُحذف الصف — المسحوب واقعة تبقى مقروءة."""
    active_school, redirect_response = _require_manager_school(request)
    if redirect_response is not None:
        return redirect_response

    delegation = get_object_or_404(Delegation, pk=pk, school=active_school)
    if delegation.revoked_at is not None:
        messages.info(request, "هذا التفويض مسحوب أصلاً.")
    else:
        delegation.revoke(by=request.user)
        messages.success(request, f"تم سحب تفويض {delegation.delegate.name}.")
    return redirect("reports:staff_roles")
