# reports/views/platform.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from ._helpers import *
from ._helpers import (
    _is_staff, _parse_date_safe, _set_active_school,
    _get_active_school, _user_manager_schools,
)


# =========================
# المشرف العام (عرض + تواصل فقط)
# =========================


def _require_platform_admin_or_superuser(request: HttpRequest) -> bool:
    return bool(getattr(request.user, "is_superuser", False) or is_platform_admin(request.user))


def _require_platform_school_access(request: HttpRequest, school: Optional[School]) -> bool:
    if getattr(request.user, "is_superuser", False):
        return True
    return bool(is_platform_admin(request.user) and platform_can_access_school(request.user, school))


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def platform_schools_directory(request: HttpRequest) -> HttpResponse:
    user = request.user
    if not _require_platform_admin_or_superuser(request):
        messages.error(request, "لا تملك صلاحية الوصول إلى شاشة المدارس.")
        return redirect("reports:home")

    # السوبر يوزر يرى كل المدارس، المشرف العام يرى المدارس ضمن نطاقه.
    base_qs = School.objects.all().order_by("name") if getattr(user, "is_superuser", False) else platform_allowed_schools_qs(user)

    q = (request.GET.get("q") or "").strip()
    gender = (request.GET.get("gender") or "").strip().lower()
    city = (request.GET.get("city") or "").strip()

    # قائمة المدن من كامل النطاق (قبل فلترة city) حتى تبقى القائمة مفيدة.
    try:
        cities = (
            base_qs.exclude(city__isnull=True)
            .exclude(city__exact="")
            .values_list("city", flat=True)
            .distinct()
            .order_by("city")
        )
        cities = list(cities)
    except Exception:
        cities = []

    qs = base_qs
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(code__icontains=q) | Q(city__icontains=q))
    if gender in {"boys", "girls"}:
        qs = qs.filter(gender=gender)
    if city:
        qs = qs.filter(city=city)

    ctx = {
        "schools": list(qs.order_by("name")),
        "cities": cities,
        "q": q,
        "gender": gender,
        "city": city,
    }
    return render(request, "reports/platform_schools_directory.html", ctx)


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def platform_enter_school(request: HttpRequest, pk: int) -> HttpResponse:
    user = request.user
    if not _require_platform_admin_or_superuser(request):
        raise Http404("ليس لديك صلاحية")

    if getattr(user, "is_superuser", False):
        school = get_object_or_404(School, pk=pk)
    else:
        school = get_object_or_404(platform_allowed_schools_qs(user), pk=pk)

    _set_active_school(request, school)
    return redirect("reports:platform_school_dashboard")


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def platform_school_dashboard(request: HttpRequest) -> HttpResponse:
    if not _require_platform_admin_or_superuser(request):
        messages.error(request, "لا تملك صلاحية الوصول إلى لوحة المدرسة.")
        return redirect("reports:home")

    active_school = _get_active_school(request)
    if active_school is None:
        return redirect("reports:platform_schools_directory")

    if not _require_platform_school_access(request, active_school):
        try:
            request.session.pop("active_school_id", None)
        except Exception:
            pass
        messages.error(request, "هذه المدرسة خارج نطاق صلاحياتك.")
        return redirect("reports:platform_schools_directory")

    subscription = (
        SchoolSubscription.objects.filter(school=active_school)
        .select_related("plan")
        .first()
    )

    return render(
        request,
        "reports/platform_school_dashboard.html",
        {"school": active_school, "subscription": subscription},
    )


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def platform_school_reports(request: HttpRequest) -> HttpResponse:
    if not _require_platform_admin_or_superuser(request):
        messages.error(request, "لا تملك صلاحية الوصول.")
        return redirect("reports:home")

    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:platform_schools_directory")

    if not _require_platform_school_access(request, active_school):
        messages.error(request, "هذه المدرسة خارج نطاق صلاحياتك.")
        return redirect("reports:platform_schools_directory")

    cats = allowed_categories_for(request.user, active_school)
    qs = get_admin_reports_queryset(user=request.user, active_school=active_school)

    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))
    teacher_name = (request.GET.get("teacher_name") or "").strip()
    category = (request.GET.get("category") or "").strip().lower()

    qs = apply_admin_report_filters(
        qs,
        start_date=start_date,
        end_date=end_date,
        teacher_name=teacher_name,
        category=category,
        cats=cats,
    )

    allowed_choices = get_reporttype_choices(active_school=active_school) if (HAS_RTYPE and ReportType is not None) else []
    reports_page = svc_paginate(qs, per_page=20, page=request.GET.get("page", 1))

    context = {
        "reports": reports_page,
        "start_date": request.GET.get("start_date", ""),
        "end_date": request.GET.get("end_date", ""),
        "teacher_name": teacher_name,
        "category": category if (not cats or "all" in cats or category in cats) else "",
        "categories": allowed_choices,
        "can_delete": False,
    }
    return render(request, "reports/admin_reports.html", context)


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def platform_school_tickets(request: HttpRequest) -> HttpResponse:
    if not _require_platform_admin_or_superuser(request):
        messages.error(request, "لا تملك صلاحية الوصول.")
        return redirect("reports:home")

    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:platform_schools_directory")

    if not _require_platform_school_access(request, active_school):
        messages.error(request, "هذه المدرسة خارج نطاق صلاحياتك.")
        return redirect("reports:platform_schools_directory")

    qs = (
        Ticket.objects.select_related("creator", "assignee", "department")
        .prefetch_related("recipients")
        .filter(school=active_school, is_platform=False)
        .order_by("-created_at")
    )

    status = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()
    mine = request.GET.get("mine") == "1"

    if status:
        qs = qs.filter(status=status)
    if mine:
        qs = qs.filter(Q(assignee=request.user) | Q(recipients=request.user)).distinct()
    if q:
        for kw in q.split():
            qs = qs.filter(Q(title__icontains=kw) | Q(body__icontains=kw))

    ctx = {
        "tickets": list(qs[:200]),
        "status": status,
        "q": q,
        "mine": mine,
        "status_choices": Ticket.Status.choices,
    }
    return render(request, "reports/tickets_inbox.html", ctx)


@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def manager_school_tickets(request: HttpRequest) -> HttpResponse:
    """قائمة جميع طلبات المدرسة للمدير (مع فلترة وبحث)."""
    active_school = _get_active_school(request)

    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية كمدير على هذه المدرسة.")
            return redirect("reports:select_school")

    qs = (
        Ticket.objects.select_related("creator", "assignee", "department")
        .prefetch_related("recipients")
        .filter(school=active_school, is_platform=False)
        .order_by("-created_at")
    )

    status = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()
    mine = request.GET.get("mine") == "1"

    if status:
        qs = qs.filter(status=status)
    if mine:
        qs = qs.filter(Q(assignee=request.user) | Q(recipients=request.user)).distinct()
    if q:
        for kw in q.split():
            qs = qs.filter(Q(title__icontains=kw) | Q(body__icontains=kw))

    ctx = {
        "tickets": list(qs[:200]),
        "status": status,
        "q": q,
        "mine": mine,
        "status_choices": Ticket.Status.choices,
        "page_title": "طلبات المدرسة",
        "page_heading": "📌 طلبات المدرسة",
        "page_subtitle": "استعرض جميع الطلبات التابعة للمدرسة، ويمكنك إضافة ملاحظات وتغيير الحالة من داخل الطلب.",
    }
    return render(request, "reports/tickets_inbox.html", ctx)


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def platform_school_notify(request: HttpRequest) -> HttpResponse:
    if not _require_platform_admin_or_superuser(request):
        messages.error(request, "لا تملك صلاحية الوصول.")
        return redirect("reports:home")

    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:platform_schools_directory")

    if not _require_platform_school_access(request, active_school):
        messages.error(request, "هذه المدرسة خارج نطاق صلاحياتك.")
        return redirect("reports:platform_schools_directory")

    form = PlatformSchoolNotificationForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        title = (form.cleaned_data.get("title") or "").strip()
        message_text = form.cleaned_data["message"]
        is_important = bool(form.cleaned_data.get("is_important"))

        try:
            with transaction.atomic():
                n = Notification.objects.create(
                    title=title,
                    message=message_text,
                    is_important=is_important,
                    school=active_school,
                    created_by=request.user,
                )
                teacher_ids = list(
                    SchoolMembership.objects.filter(
                        school=active_school,
                        is_active=True,
                        teacher__is_active=True,
                    )
                    .values_list("teacher_id", flat=True)
                    .distinct()
                )
                recipients = [NotificationRecipient(notification=n, teacher_id=tid) for tid in teacher_ids]
                NotificationRecipient.objects.bulk_create(recipients, ignore_conflicts=True)

                # Push WS delta (bulk_create doesn't trigger signals)
                try:
                    from ..realtime_notifications import push_new_notification_to_teachers

                    push_new_notification_to_teachers(notification=n, teacher_ids=teacher_ids)
                except Exception:
                    pass
            messages.success(request, "تم إرسال الإشعار إلى جميع مستخدمي المدرسة.")
            return redirect("reports:platform_school_dashboard")
        except Exception:
            logger.exception("Failed to send school notification")
            messages.error(request, "تعذّر إرسال الإشعار. حاول مرة أخرى.")

    return render(request, "reports/platform_school_notify.html", {"form": form, "school": active_school})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def platform_admin_create(request: HttpRequest) -> HttpResponse:
    from ..models import PlatformAdminScope

    form = PlatformAdminCreateForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                admin_user = form.save(commit=True)

                role_obj = form.cleaned_data.get("role")
                gender_scope = (form.cleaned_data.get("gender_scope") or "all").strip().lower()
                cities_raw = (form.cleaned_data.get("cities") or "").strip()
                allowed_schools = form.cleaned_data.get("allowed_schools")

                cities_list = []
                if cities_raw:
                    for part in cities_raw.replace("؛", ",").split(","):
                        c = (part or "").strip()
                        if c and c not in cities_list:
                            cities_list.append(c)

                scope, _created = PlatformAdminScope.objects.get_or_create(admin=admin_user)
                scope.role = role_obj
                scope.gender_scope = gender_scope if gender_scope in {"all", "boys", "girls"} else "all"
                scope.allowed_cities = cities_list
                scope.save()
                if allowed_schools is not None:
                    scope.allowed_schools.set(list(allowed_schools))

            messages.success(request, "تم إنشاء مشرف المنصة بنجاح.")
            return redirect("reports:platform_admin_dashboard")
        except Exception:
            logger.exception("Failed to create platform admin")
            messages.error(request, "تعذّر إنشاء مشرف المنصة. تحقق من البيانات.")

    return render(request, "reports/platform_admin_create.html", {"form": form})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET"])
def platform_admins_list(request: HttpRequest) -> HttpResponse:
    from ..models import PlatformAdminScope

    q = (request.GET.get("q") or "").strip()
    qs = (
        Teacher.objects.filter(is_platform_admin=True)
        .select_related("platform_scope")
        .prefetch_related("platform_scope__allowed_schools")
        .order_by("name", "id")
    )
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q))

    # تأكيد وجود scope لكل مشرف (اختياري/مساعد)
    try:
        missing_ids = list(qs.filter(platform_scope__isnull=True).values_list("id", flat=True))
        if missing_ids:
            for tid in missing_ids:
                try:
                    PlatformAdminScope.objects.get_or_create(admin_id=tid)
                except Exception:
                    pass
            qs = (
                Teacher.objects.filter(is_platform_admin=True)
                .select_related("platform_scope")
                .prefetch_related("platform_scope__allowed_schools")
                .order_by("name", "id")
            )
            if q:
                qs = qs.filter(Q(name__icontains=q) | Q(phone__icontains=q))
    except Exception:
        pass

    return render(request, "reports/platform_admins_list.html", {"admins": list(qs), "q": q})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def platform_admin_update(request: HttpRequest, pk: int) -> HttpResponse:
    from ..models import PlatformAdminScope

    admin_user = get_object_or_404(Teacher, pk=pk, is_platform_admin=True)
    scope, _created = PlatformAdminScope.objects.get_or_create(admin=admin_user)

    form = PlatformAdminCreateForm(request.POST or None, instance=admin_user)

    if request.method == "POST" and form.is_valid():
        try:
            with transaction.atomic():
                updated_user = form.save(commit=True)

                role_obj = form.cleaned_data.get("role")
                gender_scope = (form.cleaned_data.get("gender_scope") or "all").strip().lower()
                cities_raw = (form.cleaned_data.get("cities") or "").strip()
                allowed_schools = form.cleaned_data.get("allowed_schools")

                cities_list = []
                if cities_raw:
                    for part in cities_raw.replace("؛", ",").split(","):
                        c = (part or "").strip()
                        if c and c not in cities_list:
                            cities_list.append(c)

                scope.admin = updated_user
                scope.role = role_obj
                scope.gender_scope = gender_scope if gender_scope in {"all", "boys", "girls"} else "all"
                scope.allowed_cities = cities_list
                scope.save()
                if allowed_schools is not None:
                    scope.allowed_schools.set(list(allowed_schools))

            messages.success(request, "تم تحديث بيانات مشرف المنصة.")
            return redirect("reports:platform_admins_list")
        except Exception:
            logger.exception("Failed to update platform admin")
            messages.error(request, "تعذّر حفظ التعديلات. حاول مرة أخرى.")

    return render(
        request,
        "reports/platform_admin_edit.html",
        {
            "form": form,
            "admin_user": admin_user,
        },
    )


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def platform_admin_delete(request: HttpRequest, pk: int) -> HttpResponse:
    admin_user = get_object_or_404(Teacher, pk=pk, is_platform_admin=True)

    # حماية: لا نحذف السوبر يوزر عبر هذه الشاشة
    if getattr(admin_user, "is_superuser", False):
        messages.error(request, "لا يمكن حذف مستخدم سوبر يوزر من هنا.")
        return redirect("reports:platform_admins_list")

    if request.method == "POST":
        try:
            admin_user.delete()
            messages.success(request, "تم حذف المشرف العام.")
        except Exception:
            logger.exception("Failed to delete platform admin")
            messages.error(request, "تعذّر حذف المشرف العام.")
        return redirect("reports:platform_admins_list")

    return render(request, "reports/platform_admin_delete.html", {"admin_user": admin_user})
