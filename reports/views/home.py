# reports/views/home.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from ._helpers import *
from ._helpers import (
    _is_staff, _safe_next_url, _filter_by_school,
    _set_active_school, _get_active_school, _user_manager_schools,
    _clean_query_params, _clean_query_value,
)


@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET", "POST"])
def select_school(request: HttpRequest) -> HttpResponse:
    """شاشة اختيار المدرسة للآدمن ومديري المدارس.

    - المستخدم السوبر يوزر يشاهد جميع المدارس.
    - مدير المدرسة يشاهد فقط المدارس التي هو مدير لها.
    """

    if request.user.is_superuser:
        schools_qs = School.objects.filter(is_active=True)
    else:
        manager_schools = _user_manager_schools(request.user)
        schools_qs = School.objects.filter(id__in=[s.id for s in manager_schools], is_active=True)

    # إن لم يكن للمستخدم أي مدارس مرتبطة به نسمح له برؤية لا شيء

    if request.method == "POST":
        sid = request.POST.get("school_id")
        try:
            school = schools_qs.get(pk=sid)
            _set_active_school(request, school)
            messages.success(request, f"تم اختيار المدرسة: {school.name}")
            return redirect("reports:admin_dashboard")
        except (School.DoesNotExist, ValueError, TypeError):
            messages.error(request, "تعذّر اختيار المدرسة. فضلاً اختر مدرسة صحيحة.")

    search_query = _clean_query_value(request.GET.get("q"))
    if search_query:
        schools_qs = schools_qs.filter(
            Q(name__icontains=search_query)
            | Q(code__icontains=search_query)
            | Q(city__icontains=search_query)
            | Q(stage__icontains=search_query)
            | Q(gender__icontains=search_query)
        )

    schools_qs = schools_qs.order_by("name", "id").only(
        "id",
        "name",
        "code",
        "city",
        "stage",
        "gender",
    )
    page_obj = Paginator(schools_qs, 24).get_page(request.GET.get("page") or 1)

    context = {
        "schools": page_obj,
        "page_obj": page_obj,
        "current_school": _get_active_school(request),
        "search_query": search_query,
        "total_schools_count": page_obj.paginator.count,
        "query_params_without_page": _clean_query_params(request.GET),
    }
    return render(request, "reports/select_school.html", context)


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def switch_school(request: HttpRequest) -> HttpResponse:
    """تبديل المدرسة النشطة بسرعة من الهيدر/القائمة."""
    if request.method == "POST":
        sid = request.POST.get("school_id")
        next_raw = request.POST.get("next")
    else:
        sid = request.GET.get("school_id")
        next_raw = request.GET.get("next")

    default_next = "reports:admin_dashboard" if _is_staff(request.user) or getattr(request.user, "is_superuser", False) else "reports:home"
    next_url = _safe_next_url(next_raw) or default_next

    if not sid:
        return redirect(next_url)

    if request.user.is_superuser:
        schools_qs = School.objects.filter(is_active=True)
    else:
        # إذا كان المستخدم مدير مدرسة، نقيّد التبديل بمدارسه كمدير فقط.
        is_manager = SchoolMembership.objects.filter(
            teacher=request.user,
            role_type=SchoolMembership.RoleType.MANAGER,
            is_active=True,
        ).exists()
        if is_manager:
            schools_qs = School.objects.filter(
                is_active=True,
                memberships__teacher=request.user,
                memberships__role_type=SchoolMembership.RoleType.MANAGER,
                memberships__is_active=True,
            ).distinct()
        else:
            # غير المدير: يبقى التبديل بين جميع المدارس ذات العضوية النشطة.
            schools_qs = (
                School.objects.filter(
                    is_active=True,
                    memberships__teacher=request.user,
                    memberships__is_active=True,
                )
                .distinct()
            )

    try:
        school = schools_qs.get(pk=sid)
        _set_active_school(request, school)
        messages.success(request, f"تم اختيار المدرسة: {school.name}")
    except (School.DoesNotExist, ValueError, TypeError):
        messages.error(request, "تعذّر تبديل المدرسة. فضلاً اختر مدرسة صحيحة.")

    return redirect(next_url)

# =========================
# الرئيسية (لوحة المعلم)
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def home(request: HttpRequest) -> HttpResponse:
    # --- Platform Admin Redirect (Not Superuser) ---
    # إذا كان مشرف منصة (وليس سوبر يوزر)، وجهه لصفحة مدارسه أو المدرسة النشطة
    if is_platform_admin(request.user) and not getattr(request.user, "is_superuser", False):
        active_school = _get_active_school(request)
        if active_school:
            # توجيه للوحة المدرسة الخاصة بالمشرف
            return redirect("reports:platform_school_dashboard")
        # توجيه لدليل المدارس لاختيار مدرسة
        return redirect("reports:platform_schools_directory")
    # -----------------------------------------------

    # مدير المدرسة له رئيسية واحدة واضحة: لوحة إدارة المدرسة.
    # يحتفظ المدير بإمكانية الوصول لصفحاته الشخصية من قائمة الحساب، لكن لا
    # ينبغي أن تقوده كلمة "الرئيسية" إلى لوحة المعلم وتخلق مسارين متنافسين.
    if is_school_manager(request.user):
        return redirect("reports:admin_dashboard")

    active_school = _get_active_school(request)
    stats = {"today_count": 0, "total_count": 0, "last_title": "—"}
    req_stats = {"open": 0, "in_progress": 0, "done": 0, "rejected": 0, "total": 0}

    # إشعار التحفيز: اعرض أحدث إشعار غير مقروء فقط.
    # (يُعلّم كمقروء فقط بعد إغلاق المستخدم للرسالة من الواجهة.)
    home_notification = None
    home_notification_recipient_id: int | None = None
    try:
        if NotificationRecipient is not None and Notification is not None:
            now = timezone.now()
            nqs = (
                NotificationRecipient.objects.select_related("notification", "notification__created_by")
                .filter(teacher=request.user)
            )

            # غير مقروء فقط
            try:
                if hasattr(NotificationRecipient, "is_read"):
                    nqs = nqs.filter(is_read=False)
                elif hasattr(NotificationRecipient, "read_at"):
                    nqs = nqs.filter(read_at__isnull=True)
            except Exception:
                pass

            # عزل حسب المدرسة النشطة (مع السماح بإشعارات عامة school=NULL)
            try:
                if active_school is not None and hasattr(Notification, "school"):
                    nqs = nqs.filter(Q(notification__school=active_school) | Q(notification__school__isnull=True))
            except Exception:
                pass

            # استبعاد المنتهي
            try:
                if hasattr(Notification, "expires_at"):
                    nqs = nqs.filter(Q(notification__expires_at__gt=now) | Q(notification__expires_at__isnull=True))
            except Exception:
                pass

            rec = nqs.order_by("-created_at", "-id").first()
            if rec is not None:
                home_notification = getattr(rec, "notification", None)
                try:
                    home_notification_recipient_id = int(getattr(rec, "pk"))
                except Exception:
                    home_notification_recipient_id = None
    except Exception:
        home_notification = None
        home_notification_recipient_id = None

    try:
        my_qs = _filter_by_school(
            Report.objects.filter(teacher=request.user).only(
                "id", "title", "report_date", "day_name", "beneficiaries_count"
            ),
            active_school,
        )
        today = timezone.localdate()
        stats["total_count"] = my_qs.count()
        stats["today_count"] = my_qs.filter(report_date=today).count()
        last_report = my_qs.order_by("-report_date", "-id").first()
        stats["last_title"] = (last_report.title if last_report else "—")
        recent_reports = list(my_qs.order_by("-report_date", "-id")[:5])

        my_tickets_qs = _filter_by_school(
            Ticket.objects.filter(creator=request.user)
            .select_related("assignee", "department")
            .only("id", "title", "status", "department", "created_at", "assignee__name")
            .order_by("-created_at", "-id"),
            active_school,
        )
        agg = my_tickets_qs.aggregate(
            open=Count("id", filter=Q(status="open")),
            in_progress=Count("id", filter=Q(status="in_progress")),
            done=Count("id", filter=Q(status="done")),
            rejected=Count("id", filter=Q(status="rejected")),
            total=Count("id"),
        )
        for k in req_stats.keys():
            req_stats[k] = int(agg.get(k) or 0)
        recent_tickets = list(my_tickets_qs[:5])

        return render(
            request,
            "reports/home.html",
            {
                "stats": stats,
                "recent_reports": recent_reports[:4],
                "req_stats": req_stats,
                "recent_tickets": recent_tickets[:3],
                "active_requests_count": req_stats["open"] + req_stats["in_progress"],
                "home_notification": home_notification,
                "home_notification_recipient_id": home_notification_recipient_id,
            },
        )
    except Exception:
        logger.exception("Home view failed")
        if settings.DEBUG or os.getenv("SHOW_ERRORS") == "1":
            html = "<h2>Home exception</h2><pre>{}</pre>".format(traceback.format_exc())
            return HttpResponse(html, status=500)
        # Never redirect back to this same view: if the failure is persistent
        # the browser ends up in an endless redirect loop instead of showing a
        # readable error.
        try:
            return render(request, "reports/home.html", {
                "stats": stats,
                "recent_reports": [],
                "req_stats": req_stats,
                "recent_tickets": [],
                "active_requests_count": 0,
                "home_notification": None,
                "home_notification_recipient_id": None,
                "home_load_failed": True,
            }, status=500)
        except Exception:
            # The template itself may be the failing part; fall back to plain text.
            logger.exception("Home view fallback rendering failed")
            return HttpResponse(
                "تعذّر تحميل الصفحة الرئيسية حالياً. حاول مجدداً بعد قليل.",
                status=500,
                content_type="text/plain; charset=utf-8",
            )
