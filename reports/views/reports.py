# reports/views/reports.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from ._helpers import *
from ._helpers import (
    _is_staff, _is_staff_or_officer, _is_manager_in_school,
    _parse_date_safe, _filter_by_school, _safe_redirect,
    _private_comment_role_label, _model_has_field,
    _get_active_school, _is_report_viewer,
    _ensure_achievement_sections,
)

from ..utils import _resolve_department_for_category, _build_head_decision


# =========================
# التقارير: إضافة/عرض/إدارة
# =========================
@login_required(login_url="reports:login")
@ratelimit(key="user", rate="30/h", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def add_report(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    if request.method == "POST":
        form = ReportForm(request.POST, request.FILES, active_school=active_school)
        if form.is_valid():
            report = form.save(commit=False)
            report.teacher = request.user
            if hasattr(report, "school") and active_school is not None:
                report.school = active_school

            # حماية حقل "المنفذ": يُحفظ دائمًا باسم المستخدم الحالي ولا نقبل أي قيمة مرسلة من الفورم.
            teacher_name_final = (getattr(request.user, "name", "") or "").strip()
            if not teacher_name_final:
                teacher_name_final = (getattr(request.user, "username", "") or str(request.user) or "").strip()
            teacher_name_final = teacher_name_final[:120]
            if hasattr(report, "teacher_name"):
                report.teacher_name = teacher_name_final

            report.save()
            messages.success(request, "تم إضافة التقرير بنجاح ✅")
            return redirect("reports:my_reports")
        messages.error(request, "فضلاً تحقق من الحقول وأعد المحاولة.")
    else:
        form = ReportForm(active_school=active_school)

    return render(request, "reports/add_report.html", {"form": form})

@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_reports(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    qs = get_teacher_reports_queryset(user=request.user, active_school=active_school)
    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))
    q = request.GET.get("q", "").strip()

    qs = apply_teacher_report_filters(qs, start_date=start_date, end_date=end_date, q=q)
    stats = teacher_report_stats(qs)
    reports_page = svc_paginate(qs, per_page=10, page=request.GET.get("page", 1))

    params = request.GET.copy()
    if "page" in params:
        params.pop("page")
    qs_params = params.urlencode()

    return render(
        request,
        "reports/my_reports.html",
        {
            "reports": reports_page,
            "qs": qs_params,
            "start_date": request.GET.get("start_date", ""),
            "end_date": request.GET.get("end_date", ""),
            "q": q,
            "stats": stats,
        },
    )

@user_passes_test(_is_staff, login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def admin_reports(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
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
    
    # ✅ إضافة صلاحيات الحذف والتعديل والمشاركة لكل تقرير باستخدام الدوال الصحيحة
    # التي تراعي رؤساء الأقسام (OFFICER) وأعضاء الأقسام (TEACHER)
    user = request.user
    for report in reports_page:
        report.user_can_delete = can_delete_report(user, report, active_school=active_school)
        report.user_can_edit = can_edit_report(user, report, active_school=active_school)
        report.user_can_share = can_share_report(user, report, active_school=active_school)

    context = {
        "reports": reports_page,
        "start_date": request.GET.get("start_date", ""),
        "end_date": request.GET.get("end_date", ""),
        "teacher_name": teacher_name,
        "category": category if (not cats or "all" in cats or category in cats) else "",
        "categories": allowed_choices,
        "can_delete": True,  # للتوافق الخلفي
    }
    return render(request, "reports/admin_reports.html", context)


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def school_reports_readonly(request: HttpRequest) -> HttpResponse:
    """عرض تقارير المدرسة (عرض فقط) لمشرف التقارير المرتبط بالمدرسة."""
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر/حدّد مدرسة أولاً.")
        return redirect("reports:home")

    # لا نسمح بالسوبر أو الموظف هنا (لمنع خلط الصلاحيات/الحسابات)
    if getattr(request.user, "is_superuser", False) or _is_staff(request.user):
        return redirect("reports:admin_reports")

    if not _is_report_viewer(request.user, active_school):
        messages.error(request, "لا تملك صلاحية الاطلاع على تقارير هذه المدرسة.")
        return redirect("reports:home")

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
        "category": category,
        "categories": allowed_choices,
        "can_delete": False,
    }
    return render(request, "reports/admin_reports.html", context)


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def officer_reports(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    user = request.user
    if user.is_superuser:
        return redirect("reports:admin_reports")

    if not (Department is not None and DepartmentMembership is not None):
        messages.error(request, "صلاحيات المسؤول تتطلب تفعيل الأقسام وعضوياتها.")
        return redirect("reports:home")

    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    officer_memberships_qs = DepartmentMembership.objects.select_related("department").filter(
        teacher=user,
        role_type=DM_OFFICER,
        department__is_active=True,
        department__school=active_school,
    )
    membership = officer_memberships_qs.first()

    # ✅ يلزم أن تكون مسؤولاً داخل المدرسة النشطة نفسها (بدون fallback عبر مدرسة أخرى)
    if membership is None:
        messages.error(request, "لا تملك صلاحية مسؤول قسم.")
        return redirect("reports:home")

    dept = membership.department if membership else None

    # ✅ الأنواع المسموحة لمسؤول القسم = اتحاد reporttypes لأقسامه داخل المدرسة النشطة
    allowed_cats_qs = None
    if HAS_RTYPE and ReportType is not None:
        allowed_cats_qs = (
            ReportType.objects.filter(
                is_active=True,
                departments__memberships__teacher=user,
                departments__memberships__role_type=DM_OFFICER,
                departments__school=active_school,
            )
            .distinct()
            .order_by("order", "name")
        )

    if allowed_cats_qs is None or not allowed_cats_qs.exists():
        messages.info(request, "لم يتم ربط قسمك بأي أنواع تقارير بعد.")
        empty_page = Paginator(Report.objects.none(), 25).get_page(1)
        return render(
            request,
            "reports/officer_reports.html",
            {
                "reports": empty_page,
                "categories": [],
                "category": "",
                "teacher_name": "",
                "start_date": "",
                "end_date": "",
                "department": dept,
            },
        )

    start_date = request.GET.get("start_date") or ""
    end_date = request.GET.get("end_date") or ""
    teacher_name = request.GET.get("teacher_name", "").strip()
    category = request.GET.get("category") or ""

    qs = Report.objects.select_related("teacher", "category", "school").filter(category__in=allowed_cats_qs)
    qs = _filter_by_school(qs, active_school)

    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)
    if teacher_name:
        qs = qs.filter(Q(teacher__name__icontains=teacher_name) | Q(teacher_name__icontains=teacher_name))
    if category:
        qs = qs.filter(category_id=category)

    qs = qs.order_by("-report_date", "-created_at")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)
    
    # ✅ إضافة صلاحيات الحذف والمشاركة لكل تقرير (بدون N+1 على قاعدة البيانات)
    is_superuser = bool(getattr(user, "is_superuser", False))
    is_platform = bool(is_platform_admin(user))

    allowed_category_ids = set()
    try:
        allowed_category_ids = set(allowed_cats_qs.values_list("id", flat=True))
    except Exception:
        allowed_category_ids = set()

    manager_school_ids = set()
    if (not is_superuser) and (not is_platform):
        try:
            manager_school_ids = set(
                SchoolMembership.objects.filter(
                    teacher=user,
                    role_type=SchoolMembership.RoleType.MANAGER,
                    is_active=True,
                ).values_list("school_id", flat=True)
            )
        except Exception:
            manager_school_ids = set()

    for report in page_obj:
        if is_superuser:
            allowed = True
        elif is_platform:
            allowed = False
        else:
            allowed = bool(
                getattr(report, "teacher_id", None) == getattr(user, "id", None)
                or (getattr(report, "school_id", None) in manager_school_ids)
                or (getattr(report, "category_id", None) in allowed_category_ids)
            )
        report.user_can_delete = allowed
        report.user_can_share = allowed

    categories_choices = [(str(c.pk), c.name) for c in allowed_cats_qs.order_by("order", "name")]

    return render(
        request,
        "reports/officer_reports.html",
        {
            "reports": page_obj,
            "categories": categories_choices,
            "category": category,
            "teacher_name": teacher_name,
            "start_date": start_date,
            "end_date": end_date,
            "department": dept,
        },
    )


# =========================
# تقارير القسم للأعضاء (عرض + طباعة فقط)
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def department_reports(request: HttpRequest) -> HttpResponse:
    """تقارير القسم لأعضاء القسم (TEACHER) - بدون حذف/مشاركة."""
    active_school = _get_active_school(request)
    user = request.user

    if getattr(user, "is_superuser", False):
        return redirect("reports:admin_reports")

    if not (Department is not None and DepartmentMembership is not None):
        messages.error(request, "عرض تقارير الأقسام يتطلب تفعيل الأقسام وعضوياتها.")
        return redirect("reports:home")

    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    # لو كان مسؤول قسم، نوجهه للوحة المسؤول الحالية (تدعم الحذف/المشاركة حسب الصلاحية)
    officer_memberships_qs = DepartmentMembership.objects.select_related("department").filter(
        teacher=user,
        role_type=DM_OFFICER,
        department__is_active=True,
        department__school=active_school,
    )
    if officer_memberships_qs.exists():
        return redirect("reports:officer_reports")

    member_memberships_qs = DepartmentMembership.objects.select_related("department").filter(
        teacher=user,
        role_type=DM_TEACHER,
        department__is_active=True,
        department__school=active_school,
    )
    membership = member_memberships_qs.first()

    if membership is None:
        messages.error(request, "لا تملك صلاحية عضو قسم ضمن المدرسة الحالية.")
        return redirect("reports:home")

    dept = membership.department

    allowed_cats_qs = None
    if HAS_RTYPE and ReportType is not None:
        allowed_cats_qs = (
            ReportType.objects.filter(
                is_active=True,
                departments__memberships__teacher=user,
                departments__memberships__role_type=DM_TEACHER,
                departments__school=active_school,
            )
            .distinct()
            .order_by("order", "name")
        )

    if allowed_cats_qs is None or not allowed_cats_qs.exists():
        messages.info(request, "لم يتم ربط قسمك بأي أنواع تقارير بعد.")
        empty_page = Paginator(Report.objects.none(), 25).get_page(1)
        return render(
            request,
            "reports/officer_reports.html",
            {
                "page_title": "📄 تقارير قسمي (عرض فقط)",
                "reports": empty_page,
                "categories": [],
                "category": "",
                "teacher_name": "",
                "start_date": "",
                "end_date": "",
                "department": dept,
                "can_delete": False,
            },
        )

    start_date = request.GET.get("start_date") or ""
    end_date = request.GET.get("end_date") or ""
    teacher_name = request.GET.get("teacher_name", "").strip()
    category = request.GET.get("category") or ""

    qs = Report.objects.select_related("teacher", "category", "school").filter(category__in=allowed_cats_qs)
    qs = _filter_by_school(qs, active_school)

    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)
    if teacher_name:
        qs = qs.filter(Q(teacher__name__icontains=teacher_name) | Q(teacher_name__icontains=teacher_name))
    if category:
        qs = qs.filter(category_id=category)

    qs = qs.order_by("-report_date", "-created_at")

    paginator = Paginator(qs, 25)
    page_number = request.GET.get("page")
    page_obj = paginator.get_page(page_number)

    categories_choices = [(str(c.pk), c.name) for c in allowed_cats_qs.order_by("order", "name")]

    return render(
        request,
        "reports/officer_reports.html",
        {
            "page_title": "📄 تقارير قسمي (عرض فقط)",
            "reports": page_obj,
            "categories": categories_choices,
            "category": category,
            "teacher_name": teacher_name,
            "start_date": start_date,
            "end_date": end_date,
            "department": dept,
            "can_delete": False,
        },
    )

# =========================
# حذف تقرير (لوحة المدير)
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def admin_delete_report(request: HttpRequest, pk: int) -> HttpResponse:
    """
    حذف تقرير مع التحقق من الصلاحيات.
    يسمح للأشخاص التالية بالحذف:
    - السوبر
    - مدير المدرسة
    - رئيس القسم (OFFICER) للتقارير المرتبطة بقسمه
    - صاحب التقرير نفسه
    
    ✅ عضو القسم (TEACHER) لا يستطيع الحذف (عرض فقط)
    ✅ مشرف المنصة لا يستطيع الحذف (عرض فقط)
    """
    active_school = _get_active_school(request)
    user = request.user
    
    try:
        # جلب التقرير مع احترام المدرسة النشطة
        qs = Report.objects.all()
        qs = _filter_by_school(qs, active_school)
        report = get_object_or_404(qs, pk=pk)
        
        # التحقق من صلاحية الحذف
        if not can_delete_report(user, report, active_school=active_school):
            messages.error(request, "لا تملك صلاحية حذف هذا التقرير.")
            return _safe_redirect(request, "reports:admin_reports")
        
        report.delete()
        messages.success(request, "🗑️ تم حذف التقرير بنجاح.")
    except Exception:
        messages.error(request, "تعذّر حذف التقرير.")
    
    return _safe_redirect(request, "reports:admin_reports")

# =========================
# حذف تقرير (لوحة المسؤول Officer)
# =========================
@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["POST"])
def officer_delete_report(request: HttpRequest, pk: int) -> HttpResponse:
    """
    حذف تقرير من قبل:
    - رئيس القسم (OFFICER) للتقارير المرتبطة بقسمه
    - مدير المدرسة
    - السوبر
    
    ✅ عضو القسم (TEACHER) لا يستطيع الحذف (عرض فقط)
    """
    active_school = _get_active_school(request)
    user = request.user
    
    try:
        r = _get_report_for_user_or_404(request, pk)
        
        # التحقق من الصلاحية
        if not can_delete_report(user, r, active_school=active_school):
            messages.error(request, "لا تملك صلاحية حذف هذا التقرير.")
            return _safe_redirect(request, "reports:admin_reports")
        
        r.delete()
        messages.success(request, "🗑️ تم حذف التقرير بنجاح.")
    except Exception:
        messages.error(request, "تعذّر حذف التقرير أو لا تملك صلاحية لذلك.")
    
    return _safe_redirect(request, "reports:admin_reports")

# =========================
# الوصول إلى تقرير معيّن (مع احترام المدرسة النشطة)
# =========================
def _get_report_for_user_or_404(request: HttpRequest, pk: int):
    active_school = _get_active_school(request)
    return svc_get_report_for_user_or_404(user=request.user, pk=pk, active_school=active_school)


# =========================
# طباعة التقرير (نسخة مُحسّنة)
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def report_print(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        active_school = _get_active_school(request)
        user = request.user

        # ✅ المدير/الموظف/السوبر يجب أن يستطيع طباعة أي تقرير ضمن نطاق المدرسة النشطة
        if getattr(user, "is_superuser", False) or _is_staff(user):
            qs = Report.objects.select_related("teacher", "category")
            if (not getattr(user, "is_superuser", False)) and active_school is None:
                messages.error(request, "فضلاً اختر مدرسة أولاً.")
                return redirect("reports:select_school")
            if active_school is not None and _model_has_field(Report, "school"):
                qs = qs.filter(school=active_school)
            r = get_object_or_404(qs, pk=pk)
        elif is_platform_admin(user):
            qs = Report.objects.select_related("teacher", "category")
            if _model_has_field(Report, "school"):
                allowed_ids = list(platform_allowed_schools_qs(user).values_list("id", flat=True))
                qs = qs.filter(school_id__in=allowed_ids)
                if active_school is not None:
                    qs = qs.filter(school=active_school)
            r = get_object_or_404(qs, pk=pk)
        else:
            r = _get_report_for_user_or_404(request, pk)

        school_scope = getattr(r, "school", None) or active_school

        # ===== تعليقات خاصة (تظهر للمعلم فقط) =====
        show_comments = False
        is_report_owner = False
        can_add_private_comment = False
        private_comments = TeacherPrivateComment.objects.none()
        comment_form = None
        try:
            is_report_owner = getattr(r, "teacher_id", None) == getattr(user, "id", None)
            is_allowed_platform = bool(is_platform_admin(user) and platform_can_access_school(user, school_scope))
            is_manager = _is_manager_in_school(user, school_scope)
            is_staff_user = _is_staff(user)
            can_add_private_comment = bool(is_allowed_platform or is_manager or is_staff_user)
            show_comments = bool(is_report_owner or can_add_private_comment)

            # عرض سجل التعليقات للمعلم + أصحاب الصلاحية (ولا تظهر في الطباعة/المشاركة)
            if is_report_owner or can_add_private_comment:
                private_comments = (
                    TeacherPrivateComment.objects.select_related("created_by")
                    .filter(report=r, teacher=getattr(r, "teacher", None))
                    .order_by("-created_at", "-id")
                )

            try:
                if private_comments is not None:
                    for c in private_comments:
                        try:
                            c.created_by_role_label = _private_comment_role_label(getattr(c, "created_by", None), school_scope)
                        except Exception:
                            c.created_by_role_label = ""
            except Exception:
                pass

            # السماح بإضافة تعليق (يصل للمعلم فقط)
            if can_add_private_comment:
                if request.method == "POST":
                    action = (request.POST.get("action") or "").strip() or "private_comment_create"

                    # create (default)
                    if action == "private_comment_create":
                        comment_form = PrivateCommentForm(request.POST)
                        if comment_form.is_valid():
                            body = comment_form.cleaned_data["body"]
                            with transaction.atomic():
                                TeacherPrivateComment.objects.create(
                                    teacher=r.teacher,
                                    created_by=user,
                                    school=school_scope,
                                    report=r,
                                    body=body,
                                )
                                n = Notification.objects.create(
                                    title="تعليق خاص على تقرير",
                                    message=body,
                                    is_important=True,
                                    school=school_scope,
                                    created_by=user,
                                )
                                NotificationRecipient.objects.create(notification=n, teacher=r.teacher)
                            return redirect(request.get_full_path())

                    # update/delete (only comment author, or superuser)
                    if action in {"private_comment_update", "private_comment_delete"}:
                        comment_id = request.POST.get("comment_id")
                        try:
                            comment_id_int = int(comment_id) if comment_id else None
                        except (TypeError, ValueError):
                            comment_id_int = None

                        if not comment_id_int:
                            return redirect(request.get_full_path())

                        comment = TeacherPrivateComment.objects.filter(
                            pk=comment_id_int,
                            report=r,
                            teacher=getattr(r, "teacher", None),
                        ).first()
                        if comment is None:
                            return redirect(request.get_full_path())

                        is_owner_of_comment = getattr(comment, "created_by_id", None) == getattr(user, "id", None)

                        if action == "private_comment_update":
                            # تعديل: لصاحب التعليق فقط
                            if not is_owner_of_comment:
                                return HttpResponse(status=403)

                        if action == "private_comment_delete":
                            # حذف: لصاحب التعليق فقط، والسوبر يمكنه حذف أي تعليق
                            if not (is_owner_of_comment or getattr(user, "is_superuser", False)):
                                return HttpResponse(status=403)

                        if action == "private_comment_delete":
                            try:
                                comment.delete()
                            except Exception:
                                pass
                            return redirect("reports:report_print", pk=r.pk)

                        body = (request.POST.get("body") or "").strip()
                        if body:
                            try:
                                TeacherPrivateComment.objects.filter(pk=comment.pk).update(body=body)
                            except Exception:
                                pass
                        return redirect(request.get_full_path())
                else:
                    comment_form = PrivateCommentForm()
        except Exception:
            show_comments = False
            is_report_owner = False
            can_add_private_comment = False
            private_comments = TeacherPrivateComment.objects.none()
            comment_form = None

        # اختيار القسم يدويًا عبر ?dept=slug-or-id (اختياري)
        dept = None
        if Department is not None:
            pref = request.GET.get("dept")
            if pref:
                dept_qs = Department.objects.all()
                try:
                    if school_scope is not None and "school" in [f.name for f in Department._meta.get_fields()]:
                        dept_qs = dept_qs.filter(school=school_scope)
                except Exception:
                    pass

                dept = dept_qs.filter(Q(slug=pref) | Q(id=pref)).first() or dept

                # لا نسمح باختيار قسم لا يرتبط بتصنيف التقرير
                cat = getattr(r, "category", None)
                if dept is not None and cat is not None:
                    try:
                        if hasattr(dept, "reporttypes") and getattr(cat, "pk", None) is not None:
                            if not dept.reporttypes.filter(pk=cat.pk).exists():
                                dept = None
                    except Exception:
                        dept = None

        if dept is None:
            cat = getattr(r, "category", None)
            dept = _resolve_department_for_category(cat, school_scope)
            # حماية إضافية: تأكد أن القسم من نفس مدرسة التقرير/المدرسة النشطة
            if dept is not None and school_scope is not None:
                try:
                    dept_school = getattr(dept, "school", None)
                    if dept_school is not None and dept_school != school_scope:
                        dept = None
                except Exception:
                    dept = None

        head_decision = _build_head_decision(dept)

        # اسم مدير المدرسة
        school_principal = ""
        try:
            school_for_principal = getattr(r, "school", None) or _get_active_school(request)
            if school_for_principal is not None:
                principal_membership = (
                    SchoolMembership.objects.select_related("teacher")
                    .filter(
                        school=school_for_principal,
                        role_type=SchoolMembership.RoleType.MANAGER,
                        is_active=True,
                    )
                    .order_by("-id")
                    .first()
                )
                if principal_membership and principal_membership.teacher:
                    school_principal = getattr(principal_membership.teacher, "name", "") or ""
        except Exception:
            school_principal = ""

        if not school_principal:
            school_principal = getattr(settings, "SCHOOL_PRINCIPAL", "")

        # إعدادات المدرسة (الاسم + المرحلة + الشعار)
        school_name = getattr(school_scope, "name", "") if school_scope else getattr(settings, "SCHOOL_NAME", "منصة التقارير المدرسية")
        school_stage = ""
        school_logo_url = ""
        if school_scope:
            try:
                school_stage = getattr(school_scope, "get_stage_display", lambda: "")() or ""
            except Exception:
                school_stage = getattr(school_scope, "stage", "") or ""
            # تم حذف شعارات المدارس (logo_file/logo_url) نهائيًا من النظام
            school_logo_url = ""

        moe_logo_url = (getattr(settings, "MOE_LOGO_URL", "") or "").strip()
        # Optional fallback: allow providing a static path via env/settings
        if not moe_logo_url:
            try:
                moe_logo_static_path = (getattr(settings, "MOE_LOGO_STATIC", "") or "").strip()
                if moe_logo_static_path:
                    moe_logo_url = static(moe_logo_static_path)
            except Exception:
                moe_logo_url = ""

        # Final fallback: always use the bundled ministry logo for printing
        if not moe_logo_url:
            moe_logo_url = static("img/UntiTtled-1.png")

        # تحديد URL الرجوع الذكي حسب دور المستخدم
        back_url = "reports:my_reports"  # الافتراضي للمعلم
        is_manager = _is_manager_in_school(user, school_scope)
        is_staff_user = _is_staff(user)
        is_superuser_val = bool(getattr(user, "is_superuser", False))
        
        if is_superuser_val or is_manager or is_staff_user:
            back_url = "reports:admin_reports"
        
        return render(
            request,
            "reports/report_print.html",
            {
                "r": r,
                "head_decision": head_decision,
                "SCHOOL_PRINCIPAL": school_principal,
                "SCHOOL_NAME": school_name,
                "SCHOOL_STAGE": school_stage,
                "SCHOOL_LOGO_URL": school_logo_url,
                "MOE_LOGO_URL": moe_logo_url,
                "show_comments": show_comments,
                "is_report_owner": is_report_owner,
                "can_add_private_comment": can_add_private_comment,
                "current_user_id": getattr(user, "id", None),
                "is_superuser": is_superuser_val,
                "private_comments": private_comments,
                "comment_form": comment_form,
                "back_url": back_url,
            },
        )
    except Http404:
        raise
    except Exception as e:
        logger.exception(f"Error in report_print view for report {pk}: {e}")
        return render(request, "500.html", {"error": str(e)}, status=500)


def _valid_sharelink_or_404(token: str, *, kind: str) -> ShareLink:
    link = (
        ShareLink.objects.select_related("report", "achievement_file", "school")
        .filter(token=token, kind=kind)
        .first()
    )
    if not link or (not link.is_active) or link.is_expired:
        raise Http404
    return link


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def report_share_manage(request: HttpRequest, pk: int) -> HttpResponse:
    """
    تفعيل/إلغاء مشاركة تقرير عبر رابط عام صالح لمدة محددة.
    
    الصلاحيات:
    - صاحب التقرير
    - مدير المدرسة
    - رئيس القسم (OFFICER) للتقارير المرتبطة بقسمه
    - السوبر
    
    ✅ عضو القسم (TEACHER) لا يستطيع المشاركة (عرض فقط)
    """
    active_school = _get_active_school(request)
    user = request.user
    
    report = get_object_or_404(Report.objects.select_related("school"), pk=pk)
    
    # التحقق من الصلاحية
    if not can_share_report(user, report, active_school=active_school):
        messages.error(request, "لا تملك صلاحية مشاركة هذا التقرير.")
        return redirect("reports:admin_reports" if _is_staff(user) else "reports:my_reports")

    expiry_days = get_share_link_default_days(school=report.school)

    now = timezone.now()
    active_link = (
        ShareLink.objects.filter(
            kind=ShareLink.Kind.REPORT,
            report=report,
            is_active=True,
            expires_at__gt=now,
        )
        .order_by("-id")
        .first()
    )

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()
        if action == "enable":
            with transaction.atomic():
                ShareLink.objects.filter(kind=ShareLink.Kind.REPORT, report=report, is_active=True).update(is_active=False)

                created = None
                for _ in range(6):
                    token = ShareLink.generate_token()
                    try:
                        created = ShareLink.objects.create(
                            token=token,
                            kind=ShareLink.Kind.REPORT,
                            created_by=request.user,
                            school=getattr(report, "school", None),
                            report=report,
                            expires_at=ShareLink.default_expires_at(),
                            is_active=True,
                        )
                        break
                    except IntegrityError:
                        created = None
                        continue

                if created is None:
                    messages.error(request, "تعذر إنشاء رابط مشاركة الآن. حاول مرة أخرى.")
                    return redirect("reports:report_share_manage", pk=report.pk)

            public_url = request.build_absolute_uri(reverse("reports:share_public", args=[created.token]))
            messages.success(
                request,
                f"تم تفعيل مشاركة التقرير ✅ (الرابط صالح لمدة {expiry_days} أيام حتى {timezone.localtime(created.expires_at).strftime('%Y-%m-%d %H:%M')})",
            )
            messages.info(request, f"رابط المشاركة: {public_url}")
            return redirect("reports:report_share_manage", pk=report.pk)

        if action == "disable" and active_link is not None:
            ShareLink.objects.filter(pk=active_link.pk).update(is_active=False)
            messages.success(request, "تم إلغاء رابط المشاركة ✅")
            return redirect("reports:report_share_manage", pk=report.pk)

        messages.error(request, "طلب غير صالح.")
        return redirect("reports:report_share_manage", pk=report.pk)

    public_url = ""
    expires_at_str = ""
    if active_link is not None:
        public_url = request.build_absolute_uri(reverse("reports:share_public", args=[active_link.token]))
        expires_at_str = timezone.localtime(active_link.expires_at).strftime("%Y-%m-%d %H:%M")

    return render(
        request,
        "reports/report_share_manage.html",
        {
            "report": report,
            "active_link": active_link,
            "public_url": public_url,
            "expires_at_str": expires_at_str,
            "expiry_days": expiry_days,
        },
    )


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def achievement_share_manage(request: HttpRequest, pk: int) -> HttpResponse:
    """تفعيل/إلغاء مشاركة ملف الإنجاز (PDF) عبر رابط عام صالح لمدة محددة (اختياري للمعلم)."""
    ach_file = get_object_or_404(TeacherAchievementFile.objects.select_related("school"), pk=pk, teacher=request.user)

    expiry_days = get_share_link_default_days(school=ach_file.school)

    now = timezone.now()
    active_link = (
        ShareLink.objects.filter(
            kind=ShareLink.Kind.ACHIEVEMENT,
            achievement_file=ach_file,
            is_active=True,
            expires_at__gt=now,
        )
        .order_by("-id")
        .first()
    )

    if request.method == "POST":
        action = (request.POST.get("action") or "").strip().lower()
        if action == "enable":
            with transaction.atomic():
                ShareLink.objects.filter(kind=ShareLink.Kind.ACHIEVEMENT, achievement_file=ach_file, is_active=True).update(is_active=False)

                created = None
                for _ in range(6):
                    token = ShareLink.generate_token()
                    try:
                        created = ShareLink.objects.create(
                            token=token,
                            kind=ShareLink.Kind.ACHIEVEMENT,
                            created_by=request.user,
                            school=getattr(ach_file, "school", None),
                            achievement_file=ach_file,
                            expires_at=ShareLink.default_expires_at(),
                            is_active=True,
                        )
                        break
                    except IntegrityError:
                        created = None
                        continue

                if created is None:
                    messages.error(request, "تعذر إنشاء رابط مشاركة الآن. حاول مرة أخرى.")
                    return redirect("reports:achievement_share_manage", pk=ach_file.pk)

            public_url = request.build_absolute_uri(reverse("reports:share_public", args=[created.token]))
            messages.success(
                request,
                f"تم تفعيل مشاركة ملف الإنجاز ✅ (الرابط صالح لمدة {expiry_days} أيام حتى {timezone.localtime(created.expires_at).strftime('%Y-%m-%d %H:%M')})",
            )
            messages.info(request, f"رابط المشاركة: {public_url}")
            return redirect("reports:achievement_share_manage", pk=ach_file.pk)

        if action == "disable" and active_link is not None:
            ShareLink.objects.filter(pk=active_link.pk).update(is_active=False)
            messages.success(request, "تم إلغاء رابط المشاركة ✅")
            return redirect("reports:achievement_share_manage", pk=ach_file.pk)

        messages.error(request, "طلب غير صالح.")
        return redirect("reports:achievement_share_manage", pk=ach_file.pk)

    public_url = ""
    expires_at_str = ""
    if active_link is not None:
        public_url = request.build_absolute_uri(reverse("reports:share_public", args=[active_link.token]))
        expires_at_str = timezone.localtime(active_link.expires_at).strftime("%Y-%m-%d %H:%M")

    return render(
        request,
        "reports/achievement_share_manage.html",
        {
            "file": ach_file,
            "active_link": active_link,
            "public_url": public_url,
            "expires_at_str": expires_at_str,
            "expiry_days": expiry_days,
        },
    )


@require_http_methods(["GET"])
def share_public(request: HttpRequest, token: str) -> HttpResponse:
    """عرض عام حسب توكن: تقرير كامل + الصور، أو صفحة تحميل PDF لملف الإنجاز."""
    link = ShareLink.objects.select_related("report", "achievement_file", "school").filter(token=token).first()
    if not link or (not link.is_active) or link.is_expired:
        return render(request, "reports/share_invalid.html", status=404)

    ShareLink.objects.filter(pk=link.pk).update(last_accessed_at=timezone.now())

    if link.kind == ShareLink.Kind.REPORT:
        r = link.report
        if r is None:
            return render(request, "reports/share_invalid.html", status=404)

        school_scope = getattr(r, "school", None) or getattr(link, "school", None)
        cat = getattr(r, "category", None)
        dept = _resolve_department_for_category(cat, school_scope)
        head_decision = _build_head_decision(dept)

        # اسم مدير المدرسة
        school_principal = ""
        try:
            if school_scope is not None:
                principal_membership = (
                    SchoolMembership.objects.select_related("teacher")
                    .filter(
                        school=school_scope,
                        role_type=SchoolMembership.RoleType.MANAGER,
                        is_active=True,
                    )
                    .order_by("-id")
                    .first()
                )
                if principal_membership and principal_membership.teacher:
                    school_principal = getattr(principal_membership.teacher, "name", "") or ""
        except Exception:
            school_principal = ""
        if not school_principal:
            school_principal = getattr(settings, "SCHOOL_PRINCIPAL", "")

        # إعدادات المدرسة
        school_name = getattr(school_scope, "name", "") if school_scope else getattr(settings, "SCHOOL_NAME", "منصة التقارير المدرسية")
        school_stage = ""
        school_logo_url = ""
        if school_scope:
            try:
                school_stage = getattr(school_scope, "get_stage_display", lambda: "")() or ""
            except Exception:
                school_stage = getattr(school_scope, "stage", "") or ""
            # تم حذف شعارات المدارس (logo_file/logo_url) نهائيًا من النظام
            school_logo_url = ""

        moe_logo_url = (getattr(settings, "MOE_LOGO_URL", "") or "").strip()
        if not moe_logo_url:
            try:
                moe_logo_static_path = (getattr(settings, "MOE_LOGO_STATIC", "") or "").strip()
                if moe_logo_static_path:
                    moe_logo_url = static(moe_logo_static_path)
            except Exception:
                moe_logo_url = ""

        # Final fallback: always use the bundled ministry logo for printing
        if not moe_logo_url:
            moe_logo_url = static("img/UntiTtled-1.png")

        return render(
            request,
            "reports/report_print.html",
            {
                "r": r,
                "head_decision": head_decision,
                "SCHOOL_PRINCIPAL": school_principal,
                "SCHOOL_NAME": school_name,
                "SCHOOL_STAGE": school_stage,
                "SCHOOL_LOGO_URL": school_logo_url,
                "MOE_LOGO_URL": moe_logo_url,
                "show_comments": False,
                "private_comments": [],
                "comment_form": None,
                "image1_url": reverse("reports:share_report_image", args=[token, 1]),
                "image2_url": reverse("reports:share_report_image", args=[token, 2]),
                "image3_url": reverse("reports:share_report_image", args=[token, 3]),
                "image4_url": reverse("reports:share_report_image", args=[token, 4]),
            },
        )

    if link.kind == ShareLink.Kind.ACHIEVEMENT:
        ach_file = link.achievement_file
        if ach_file is None:
            return render(request, "reports/share_invalid.html", status=404)

        # نفس تجربة مشاركة التقارير: فتح الرابط يعرض "الملف" مباشرة (صفحة طباعة/معاينة)، مع خيار تنزيل PDF.
        _ensure_achievement_sections(ach_file)
        try:
            from django.db.models import Prefetch

            ev_reports_qs = AchievementEvidenceReport.objects.select_related(
                "report",
                "report__category",
            ).order_by("id")
            sections = (
                AchievementSection.objects.filter(file=ach_file)
                .prefetch_related("evidence_images", Prefetch("evidence_reports", queryset=ev_reports_qs))
                .order_by("code", "id")
            )
            has_evidence_reports = AchievementEvidenceReport.objects.filter(section__file=ach_file).exists()
        except Exception:
            sections = (
                AchievementSection.objects.filter(file=ach_file)
                .prefetch_related("evidence_images", "evidence_reports")
                .order_by("code", "id")
            )
            has_evidence_reports = False

        school = ach_file.school
        primary = (getattr(school, "print_primary_color", None) or "").strip() or "#2563eb"

        # تم حذف شعارات المدارس (logo_file/logo_url) نهائيًا من النظام
        school_logo_url = ""

        try:
            from ..pdf_achievement import _static_png_as_data_uri

            ministry_logo_src = _static_png_as_data_uri("img/UntiTtled-1.png")
        except Exception:
            ministry_logo_src = None

        download_url = request.build_absolute_uri(reverse("reports:share_achievement_pdf", args=[token]))
        return render(
            request,
            "reports/pdf/achievement_file.html",
            {
                "file": ach_file,
                "school": school,
                "sections": sections,
                "has_evidence_reports": has_evidence_reports,
                "theme": {"brand": primary},
                "now": timezone.localtime(timezone.now()),
                "public_mode": True,
                "public_download_url": download_url,
                "school_logo_url": school_logo_url,
                "ministry_logo_src": ministry_logo_src,
            },
        )

    return render(request, "reports/share_invalid.html", status=404)


@require_http_methods(["GET"])
def share_report_image(request: HttpRequest, token: str, slot: int) -> HttpResponse:
    link = _valid_sharelink_or_404(token, kind=ShareLink.Kind.REPORT)
    r = link.report
    if r is None:
        raise Http404

    if slot not in (1, 2, 3, 4):
        raise Http404
    field = getattr(r, f"image{slot}", None)
    if not field:
        raise Http404

    try:
        f = field.open("rb")
        resp = FileResponse(f)
        try:
            filename = os.path.basename(getattr(field, "name", "") or "") or f"image{slot}"
            resp["Content-Disposition"] = f'inline; filename="{filename}"'
        except Exception:
            pass
        return resp
    except Exception:
        url = getattr(field, "url", None)
        if url:
            return redirect(url)
        raise


@require_http_methods(["GET"])
def share_achievement_pdf(request: HttpRequest, token: str) -> HttpResponse:
    link = _valid_sharelink_or_404(token, kind=ShareLink.Kind.ACHIEVEMENT)
    ach_file = link.achievement_file
    if ach_file is None:
        raise Http404

    # إذا لم يكن الـ PDF مخزنًا بعد، ولّدْه عند الطلب واحتفظ به لتعمل المشاركة دائمًا.
    if not getattr(ach_file, "pdf_file", None):
        try:
            from django.core.files.base import ContentFile
            from ..pdf_achievement import generate_achievement_pdf

            pdf_bytes, filename = generate_achievement_pdf(request=request, ach_file=ach_file)

            try:
                ach_file.pdf_file.save(filename, ContentFile(pdf_bytes), save=False)
                ach_file.pdf_generated_at = timezone.now()
                ach_file.save(update_fields=["pdf_file", "pdf_generated_at"])
            except Exception:
                # حتى لو فشل التخزين (S3/permissions..)، نُرجع الملف للمستخدم.
                pass

            resp = HttpResponse(pdf_bytes, content_type="application/pdf")
            resp["Content-Disposition"] = f'inline; filename="{filename}"'
            return resp
        except OSError as ex:
            # WeasyPrint قد يفشل بسبب مكتبات النظام (خصوصًا على Windows).
            msg = str(ex) or ""
            if "libgobject" in msg or "gobject-2.0" in msg:
                return HttpResponse(
                    "تعذر توليد ملف PDF حاليًا بسبب نقص مكتبات الطباعة على الخادم.",
                    status=503,
                    content_type="text/plain; charset=utf-8",
                )
            if settings.DEBUG:
                raise
            return HttpResponse(
                "تعذر توليد ملف PDF حاليًا.",
                status=503,
                content_type="text/plain; charset=utf-8",
            )
        except Exception:
            if settings.DEBUG:
                raise
            return HttpResponse(
                "تعذر توليد ملف PDF حاليًا.",
                status=503,
                content_type="text/plain; charset=utf-8",
            )

    try:
        f = ach_file.pdf_file.open("rb")
        resp = FileResponse(f, content_type="application/pdf")
        try:
            filename = os.path.basename(getattr(ach_file.pdf_file, "name", "") or "") or "achievement.pdf"
            resp["Content-Disposition"] = f'inline; filename="{filename}"'
        except Exception:
            pass
        return resp
    except Exception:
        url = getattr(ach_file.pdf_file, "url", None)
        if url:
            return redirect(url)
        raise


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def edit_my_report(request: HttpRequest, pk: int) -> HttpResponse:
    """
    تعديل تقرير مع التحقق من الصلاحيات.
    يسمح للأشخاص التالية بالتعديل:
    - السوبر
    - مدير المدرسة
    - رئيس القسم (OFFICER) للتقارير المرتبطة بقسمه
    - صاحب التقرير نفسه
    
    ✅ عضو القسم (TEACHER) لا يستطيع التعديل (عرض فقط)
    ✅ مشرف المنصة لا يستطيع التعديل (عرض فقط)
    """
    user = request.user
    active_school = _get_active_school(request)

    # جلب التقرير باستخدام restrict_queryset (للتأكد من أن المستخدم يستطيع رؤيته)
    qs = restrict_queryset_for_user(Report.objects.all(), user, active_school)
    qs = _filter_by_school(qs, active_school)
    r = get_object_or_404(qs, pk=pk)
    
    # التحقق من صلاحية التعديل
    if not can_edit_report(user, r, active_school=active_school):
        messages.error(request, "لا تملك صلاحية تعديل هذا التقرير.")
        return redirect("reports:admin_reports")

    # لا نجبر تغيير المدرسة النشطة بالجَلسة، لكن نستخدم مدرسة التقرير لتصفية الأنواع عند الحاجة.
    form_school = active_school
    if form_school is None and _model_has_field(Report, "school"):
        try:
            form_school = getattr(r, "school", None)
        except Exception:
            form_school = active_school

    if request.method == "POST":
        form = ReportForm(request.POST, request.FILES, instance=r, active_school=form_school)
        if form.is_valid():
            form.save()
            messages.success(request, "✏️ تم تحديث التقرير بنجاح.")
            nxt = request.POST.get("next") or request.GET.get("next")
            if nxt:
                return redirect(nxt)
            # إذا كان المستخدم ليس صاحب التقرير، يعود لـ admin_reports
            if getattr(r, "teacher_id", None) != getattr(user, "id", None):
                return redirect("reports:admin_reports")
            return redirect("reports:my_reports")
        messages.error(request, "تحقّق من الحقول.")
    else:
        form = ReportForm(instance=r, active_school=form_school)

    return render(request, "reports/edit_report.html", {"form": form, "report": r})

@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def delete_my_report(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    qs = Report.objects.filter(teacher=request.user)
    qs = _filter_by_school(qs, active_school)
    r = get_object_or_404(qs, pk=pk)
    r.delete()
    messages.success(request, "🗑️ تم حذف التقرير.")
    nxt = request.POST.get("next") or request.GET.get("next")
    return redirect(nxt or "reports:my_reports")
