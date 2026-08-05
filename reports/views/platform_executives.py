# -*- coding: utf-8 -*-
"""شاشات مدير النظام لإدارة المدراء التنفيذيين ومجموعاتهم.

هذه الشاشات ملكيةٌ خالصة لمالك المنصة: هي المكان الوحيد الذي يُنشأ فيه منصب
المدير التنفيذي وتُسنَد إليه المدارس. والمدير التنفيذي نفسه لا يملك منها شيئاً —
لوحته قراءة فقط عمداً، فمن يشرف على المدارس لا يوسّع نطاق إشرافه بنفسه.

كل عملية كتابة هنا تجري داخل معاملة واحدة، لأن إنشاء مجموعة بلا مدير أو مدير
بلا مدارس حالةٌ نصف منجزة تظهر في اللوحة كأنها صحيحة.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.db.models import Count, Prefetch, Q
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from ..forms_executive_directors import ExecutiveDirectorForm, generate_unique_group_code
from ..models import School, SchoolGroup, SchoolGroupMembership, Teacher

__all__ = [
    "platform_executive_directors",
    "platform_executive_director_form",
    "platform_executive_director_toggle",
    "platform_executive_director_delete",
]


def _is_owner(request: HttpRequest) -> bool:
    return bool(getattr(request.user, "is_superuser", False))


def _director_membership(group: SchoolGroup) -> SchoolGroupMembership | None:
    """عضوية المدير التنفيذي لهذه المجموعة، نشطة كانت أو موقوفة.

    تُعاد الموقوفة أيضاً حتى تُعرض المجموعة بمن كان يقودها بدل أن تظهر بلا
    مدير — فالفرق بين «موقوف» و«غير معيَّن» فرقٌ إداري حقيقي.
    """
    memberships = list(getattr(group, "director_memberships", []) or [])
    if not memberships:
        return None
    return next((m for m in memberships if m.is_active), memberships[0])


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def platform_executive_directors(request: HttpRequest) -> HttpResponse:
    """قائمة المدراء التنفيذيين ومجموعاتهم."""
    if not _is_owner(request):
        messages.error(request, "لا تملك صلاحية الوصول إلى شاشة المدراء التنفيذيين.")
        return redirect("reports:home")

    groups_qs = (
        SchoolGroup.objects.all()
        .select_related("headquarters_school")
        .prefetch_related(
            Prefetch(
                "memberships",
                queryset=SchoolGroupMembership.objects.filter(
                    role_type=SchoolGroupMembership.RoleType.EXECUTIVE_DIRECTOR
                )
                .select_related("user")
                .order_by("-is_active", "-created_at"),
                to_attr="director_memberships",
            ),
            Prefetch(
                "schools",
                queryset=School.objects.order_by("name"),
                to_attr="assigned_schools",
            ),
        )
        .annotate(
            schools_count=Count("schools", distinct=True),
            active_schools_count=Count("schools", filter=Q(schools__is_active=True), distinct=True),
        )
        .order_by("name")
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        groups_qs = groups_qs.filter(
            Q(name__icontains=q)
            | Q(code__icontains=q)
            | Q(education_department__icontains=q)
            | Q(memberships__user__name__icontains=q)
            | Q(memberships__user__phone__icontains=q)
        ).distinct()

    rows = []
    for group in groups_qs:
        membership = _director_membership(group)
        rows.append(
            {
                "group": group,
                "membership": membership,
                "director": getattr(membership, "user", None),
                "is_led": bool(membership and membership.is_active and group.is_active),
                "schools": getattr(group, "assigned_schools", []),
            }
        )

    stats = {
        "groups": len(rows),
        "active_directors": sum(1 for row in rows if row["is_led"]),
        "unassigned_groups": sum(1 for row in rows if row["director"] is None),
        "schools": sum(row["group"].schools_count for row in rows),
    }

    return render(
        request,
        "reports/platform_executive_directors.html",
        {"rows": rows, "stats": stats, "q": q},
    )


def _initial_from_group(group: SchoolGroup) -> dict:
    membership = (
        SchoolGroupMembership.objects.filter(
            group=group, role_type=SchoolGroupMembership.RoleType.EXECUTIVE_DIRECTOR
        )
        .select_related("user")
        .order_by("-is_active", "-created_at")
        .first()
    )
    director = getattr(membership, "user", None)
    return {
        "group_name": group.name,
        "group_code": group.code,
        "education_department": group.education_department,
        "is_active": group.is_active,
        "director_phone": getattr(director, "phone", ""),
        "director_name": getattr(director, "name", ""),
        "director_email": getattr(director, "email", ""),
        "schools": list(group.schools.values_list("pk", flat=True)),
        "headquarters_school": group.headquarters_school_id,
    }


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def platform_executive_director_form(request: HttpRequest, pk: int | None = None) -> HttpResponse:
    """إضافة مدير تنفيذي جديد أو تعديل مجموعة قائمة."""
    if not _is_owner(request):
        messages.error(request, "لا تملك صلاحية إدارة المدراء التنفيذيين.")
        return redirect("reports:home")

    group = get_object_or_404(SchoolGroup, pk=pk) if pk else None

    if request.method == "POST":
        form = ExecutiveDirectorForm(request.POST, group=group)
        if form.is_valid():
            group = _save_executive_director(form, group)
            messages.success(
                request,
                f"تم حفظ المدير التنفيذي لمجموعة «{group.name}» وإسناد "
                f"{group.schools.count()} مدرسة إليه.",
            )
            return redirect("reports:platform_executive_directors")
        messages.error(request, "تعذّر الحفظ. راجع الحقول المعلَّمة بالأخطاء.")
    else:
        form = ExecutiveDirectorForm(
            initial=_initial_from_group(group) if group else {"is_active": True},
            group=group,
        )

    return render(
        request,
        "reports/platform_executive_director_form.html",
        {"form": form, "group": group, "is_edit": group is not None},
    )


@transaction.atomic
def _save_executive_director(form: ExecutiveDirectorForm, group: SchoolGroup | None) -> SchoolGroup:
    """يحفظ المجموعة ومديرها ومدارسها كعملية واحدة."""
    data = form.cleaned_data
    is_active = bool(data.get("is_active"))

    if group is None:
        group = SchoolGroup(name=data["group_name"])
    group.name = data["group_name"]
    group.code = data.get("group_code") or generate_unique_group_code(
        data["group_name"], exclude_pk=group.pk
    )
    group.education_department = data.get("education_department") or ""
    group.is_active = is_active
    # المقر يُضبط بعد إسناد المدارس، فوضعه الآن قد يشير إلى مدرسة لم تنضم بعد.
    group.headquarters_school = None
    group.save()

    # ── المستخدم ───────────────────────────────────────────────────────
    director = form.director_user
    if director is None:
        director = Teacher.objects.create_user(
            phone=data["director_phone"],
            name=data["director_name"].strip(),
            email=data.get("director_email") or "",
            password=data["director_password"],
        )
    else:
        changed = []
        new_name = (data.get("director_name") or "").strip()
        if new_name and new_name != director.name:
            director.name = new_name
            changed.append("name")
        new_email = (data.get("director_email") or "").strip()
        if new_email and new_email != director.email:
            director.email = new_email
            changed.append("email")
        new_password = data.get("director_password") or ""
        if new_password:
            director.set_password(new_password)
            changed.append("password")
        if changed:
            director.save(update_fields=changed)

    # ── العضوية ────────────────────────────────────────────────────────
    # إيقاف من كان يقود المجموعة قبل تنشيط الجديد: قيد قاعدة البيانات يسمح
    # بمدير تنفيذي نشط واحد لكل مجموعة، وترتيب العمليتين هو ما يحفظه.
    SchoolGroupMembership.objects.filter(
        group=group, role_type=SchoolGroupMembership.RoleType.EXECUTIVE_DIRECTOR
    ).exclude(user=director).update(is_active=False)

    membership, _ = SchoolGroupMembership.objects.get_or_create(
        group=group,
        user=director,
        role_type=SchoolGroupMembership.RoleType.EXECUTIVE_DIRECTOR,
        defaults={"is_active": is_active},
    )
    if membership.is_active != is_active:
        membership.is_active = is_active
        membership.save(update_fields=["is_active"])

    # ── المدارس ────────────────────────────────────────────────────────
    selected_ids = [school.pk for school in data.get("schools") or []]
    # المدارس المرفوعة عن المجموعة تعود مستقلة، ولا يمسّ ذلك بياناتها ولا مديرها.
    group.schools.exclude(pk__in=selected_ids).update(group=None)
    if selected_ids:
        School.objects.filter(pk__in=selected_ids).update(group=group)

    headquarters = data.get("headquarters_school")
    if headquarters is not None and headquarters.pk in selected_ids:
        group.headquarters_school = headquarters
        group.save(update_fields=["headquarters_school"])

    return group


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def platform_executive_director_toggle(request: HttpRequest, pk: int) -> HttpResponse:
    """إيقاف/تفعيل المجموعة ومنصب مديرها التنفيذي معاً."""
    if not _is_owner(request):
        messages.error(request, "لا تملك صلاحية إدارة المدراء التنفيذيين.")
        return redirect("reports:home")

    group = get_object_or_404(SchoolGroup, pk=pk)
    with transaction.atomic():
        group.is_active = not group.is_active
        group.save(update_fields=["is_active"])
        # العضوية تتبع حالة المجموعة حتى لا يبقى منصبٌ نشط في مجموعة موقوفة.
        SchoolGroupMembership.objects.filter(
            group=group, role_type=SchoolGroupMembership.RoleType.EXECUTIVE_DIRECTOR
        ).update(is_active=group.is_active)

    messages.success(
        request,
        f"تم {'تفعيل' if group.is_active else 'إيقاف'} مجموعة «{group.name}» ومديرها التنفيذي.",
    )
    return redirect("reports:platform_executive_directors")


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def platform_executive_director_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """حذف المجموعة والمنصب. المدارس تعود مستقلة وحساب المدير يبقى قائماً."""
    if not _is_owner(request):
        messages.error(request, "لا تملك صلاحية إدارة المدراء التنفيذيين.")
        return redirect("reports:home")

    group = get_object_or_404(SchoolGroup, pk=pk)
    name = group.name
    released = group.schools.count()
    # الحذف يمس طبقة الإشراف وحدها: ``School.group`` يُفرَّغ بـ SET_NULL،
    # وحساب المدير التنفيذي لا يُحذف لأنه قد يُسنَد إليه منصب آخر.
    group.delete()

    messages.success(
        request,
        f"تم حذف مجموعة «{name}» وإعادة {released} مدرسة إلى وضعها المستقل. حساب المدير التنفيذي لم يُحذف.",
    )
    return redirect("reports:platform_executive_directors")
