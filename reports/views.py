# reports/views.py
# -*- coding: utf-8 -*-
from __future__ import annotations
from .models import (
    Ticket, TicketNote, TicketImage, 
    SubscriptionPlan, SchoolSubscription, Payment, SchoolMembership, NotificationRecipient
)

import logging
import os
import traceback
from datetime import date, timedelta
from typing import Optional, Tuple
from urllib.parse import urlparse

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db import IntegrityError, transaction
from django.db.models import (
    Count,
    Prefetch,
    Q,
    ManyToManyField,
    ForeignKey,
    OuterRef,
    Subquery,
    ProtectedError,
    Sum,
)
from django.core.exceptions import ValidationError
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_http_methods

# ===== فورمات =====
from .forms import (
    ReportForm,
    TeacherForm,
    TeacherCreateForm,
    TeacherEditForm,
    TicketActionForm,
    TicketCreateForm,
    DepartmentForm,  # إن لم تكن موجودة في مشروعك سيتم استخدام بديل داخلي
    ManagerCreateForm,
    SubscriptionPlanForm,
    SchoolSubscriptionForm,
)

# إشعارات (اختياري)
try:
    from .forms import NotificationCreateForm  # type: ignore
except Exception:
    NotificationCreateForm = None  # type: ignore

# ===== موديلات =====
from .models import (
    Report,
    Teacher,
    Ticket,
    TicketNote,
    Role,
    School,
    SchoolMembership,
    MANAGER_SLUG,
    SubscriptionPlan,
    SchoolSubscription,
    Payment,
)

# موديلات الإشعارات (اختياري)
try:
    from .models import Notification, NotificationRecipient  # type: ignore
except Exception:
    Notification = None  # type: ignore
    NotificationRecipient = None  # type: ignore

# موديلات مرجعية اختيارية
try:
    from .models import ReportType  # type: ignore
except Exception:  # pragma: no cover
    ReportType = None  # type: ignore

try:
    from .models import Department  # type: ignore
except Exception:  # pragma: no cover
    Department = None  # type: ignore

try:
    from .models import DepartmentMembership  # type: ignore
except Exception:  # pragma: no cover
    DepartmentMembership = None  # type: ignore

# ===== صلاحيات =====
from .permissions import allowed_categories_for, role_required, restrict_queryset_for_user
try:
    from .permissions import is_officer  # type: ignore
except Exception:
    # بديل مرن إن لم تتوفر الدالة في permissions
    def is_officer(user) -> bool:
        try:
            if not getattr(user, "is_authenticated", False):
                return False
            from .models import DepartmentMembership  # import محلي
            role_type = getattr(DepartmentMembership, "OFFICER", "officer")
            return DepartmentMembership.objects.filter(
                teacher=user, role_type=role_type, department__is_active=True
            ).exists()
        except Exception:
            return False

# ===== إعدادات محلية =====
HAS_RTYPE: bool = ReportType is not None
DM_TEACHER = getattr(DepartmentMembership, "TEACHER", "teacher") if DepartmentMembership else "teacher"
DM_OFFICER = getattr(DepartmentMembership, "OFFICER", "officer") if DepartmentMembership else "officer"

# إيقاف/تشغيل الرجوع التلقائي للحالة عند ملاحظة المرسل (افتراضي معطّل)
AUTO_REOPEN_ON_SENDER_NOTE: bool = getattr(settings, "TICKETS_AUTO_REOPEN_ON_SENDER_NOTE", False)

logger = logging.getLogger(__name__)

# =========================
# أدوات مساعدة عامة
# =========================
def _is_staff(user) -> bool:
    # ✅ دعم مدير المدرسة (School Manager)
    if not getattr(user, "is_authenticated", False):
        return False
    if getattr(user, "is_staff", False):
        return True
    try:
        return SchoolMembership.objects.filter(
            teacher=user, 
            role_type=SchoolMembership.RoleType.MANAGER,
            is_active=True
        ).exists()
    except Exception:
        return False

def _is_staff_or_officer(user) -> bool:
    """يسمح للموظّفين (is_staff) أو لمسؤولي الأقسام (Officer)."""
    return bool(getattr(user, "is_authenticated", False) and
                (getattr(user, "is_staff", False) or is_officer(user)))

def _safe_next_url(next_url: str | None) -> str | None:
    if not next_url:
        return None
    parsed = urlparse(next_url)
    if parsed.scheme == "" and parsed.netloc == "":
        return next_url
    return None

def _role_display_map() -> dict:
    base = {"teacher": "المعلم", "manager": "المدير", "officer": "مسؤول قسم"}
    if Department is not None:
        try:
            for d in Department.objects.filter(is_active=True).only("slug", "role_label", "name"):
                base[d.slug] = d.role_label or d.name or d.slug
        except Exception:
            pass
    return base

def _safe_redirect(request: HttpRequest, fallback_name: str) -> HttpResponse:
    nxt = request.POST.get("next") or request.GET.get("next")
    if nxt and url_has_allowed_host_and_scheme(nxt, {request.get_host()}):
        return redirect(nxt)
    return redirect(fallback_name)

def _parse_date_safe(value: str | None) -> date | None:
    if not value:
        return None
    return parse_date(value)


def _filter_by_school(qs, school: Optional[School]):
    """تطبيق فلتر المدرسة إذا كان للموديل حقل school وكان هناك مدرسة نشطة."""
    if not school:
        return qs
    try:
        if "school" in [f.name for f in qs.model._meta.get_fields()]:
            return qs.filter(school=school)
    except Exception:
        return qs
    return qs


def _get_active_school(request: HttpRequest) -> Optional[School]:
    """إرجاع المدرسة المختارة حالياً من الجلسة (إن وُجدت).

    تحسين احترافي:
    - إذا لم تُحدَّد مدرسة في الجلسة، وكان للمستخدم مدرسة واحدة فقط → نعتبرها المدرسة النشطة تلقائياً.
    - للمشرف العام: إن لم يكن لديه عضويات ومدرسة واحدة فقط مفعّلة في النظام → نختارها تلقائياً.
    """
    sid = request.session.get("active_school_id")
    try:
        if sid:
            return School.objects.filter(pk=sid, is_active=True).first()

        user = getattr(request, "user", None)
        # مستخدم عادي: مدرسة واحدة فقط ضمن عضوياته
        if user is not None and getattr(user, "is_authenticated", False):
            schools = _user_schools(user)
            if len(schools) == 1:
                school = schools[0]
                _set_active_school(request, school)
                return school

            # مشرف عام مع مدرسة واحدة فقط في النظام
            if getattr(user, "is_superuser", False):
                qs = School.objects.filter(is_active=True)
                if qs.count() == 1:
                    school = qs.first()
                    if school is not None:
                        _set_active_school(request, school)
                        return school
    except Exception:
        return None
    return None


def _set_active_school(request: HttpRequest, school: Optional[School]) -> None:
    """تحديث المدرسة المختارة في الجلسة للمستخدم الحالي."""
    if school is None:
        request.session.pop("active_school_id", None)
    else:
        request.session["active_school_id"] = school.pk


def _user_schools(user) -> list[School]:
    """إرجاع المدارس المرتبطة بالمستخدم عبر عضويات SchoolMembership."""
    if not getattr(user, "is_authenticated", False):
        return []
    try:
        qs = (
            School.objects.filter(memberships__teacher=user, memberships__is_active=True)
            .distinct()
            .order_by("name")
        )
        return list(qs)
    except Exception:
        return []


def _user_manager_schools(user) -> list[School]:
    """المدارس التي يكون فيها المستخدم مدير مدرسة."""
    if not getattr(user, "is_authenticated", False):
        return []
    try:
        qs = (
            School.objects.filter(
                memberships__teacher=user,
                memberships__role_type=SchoolMembership.RoleType.MANAGER,
                memberships__is_active=True,
            )
            .distinct()
            .order_by("name")
        )
        return list(qs)
    except Exception:
        return []

# =========================
# الدخول / الخروج
# =========================
@require_http_methods(["GET", "POST"])
def login_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        # إن كان المستخدم موظّف لوحة (مدير/سوبر أدمن) نوجّهه للوحة المناسبة
        if getattr(request.user, "is_superuser", False):
            return redirect("reports:platform_admin_dashboard")
        if _is_staff(request.user):
            return redirect("reports:admin_dashboard")
        return redirect("reports:home")

    if request.method == "POST":
        phone = (request.POST.get("phone") or "").strip()
        password = request.POST.get("password") or ""
        user = authenticate(request, username=phone, password=password)
        if user is not None:
            # ✅ قواعد الاشتراك عند تسجيل الدخول:
            # - السوبر: يتجاوز دائمًا.
            # - مدير المدرسة: يُسمح له بالدخول حتى لو انتهى الاشتراك، لكن يُوجّه لصفحة (انتهاء الاشتراك)
            #   ولا يستطيع استخدام المنصة إلا لصفحات التجديد (يُفرض ذلك عبر SubscriptionMiddleware).
            # - بقية المستخدمين: إن لم توجد أي مدرسة باشتراك ساري → نمنع تسجيل الدخول.

            if not getattr(user, "is_superuser", False):
                try:
                    memberships = (
                        SchoolMembership.objects.filter(teacher=user, is_active=True)
                        .select_related("school")
                        .order_by("id")
                    )

                    active_school = None
                    any_active_subscription = False
                    is_any_manager = False
                    manager_school = None
                    first_school_name = None

                    for m in memberships:
                        if first_school_name is None:
                            first_school_name = getattr(getattr(m, "school", None), "name", None)
                        if m.role_type == SchoolMembership.RoleType.MANAGER:
                            is_any_manager = True
                            if manager_school is None:
                                manager_school = m.school

                        sub = None
                        try:
                            sub = m.school.subscription
                        except Exception:
                            sub = None

                        # عدم وجود اشتراك = منتهي
                        if sub is not None and not bool(sub.is_expired) and bool(getattr(m.school, "is_active", True)):
                            any_active_subscription = True
                            if active_school is None:
                                active_school = m.school

                    if not any_active_subscription:
                        if is_any_manager and manager_school is not None:
                            # المدير يُسمح له بالدخول للتجديد فقط
                            login(request, user)
                            _set_active_school(request, manager_school)
                            return redirect("reports:subscription_expired")

                        school_label = f" ({first_school_name})" if first_school_name else ""
                        messages.error(request, f"عذرًا، اشتراك المدرسة{school_label} منتهي. لا يمكن الدخول حتى يتم تجديد الاشتراك.")
                        return redirect("reports:login")

                    # هناك اشتراك ساري واحد على الأقل → نكمل تسجيل الدخول ونثبت مدرسة نشطة مناسبة
                    login(request, user)
                    if active_school is not None:
                        _set_active_school(request, active_school)
                except Exception:
                    # في حال أي مشكلة في تحقق الاشتراك، لا نكسر تسجيل الدخول (سيتولى Middleware المنع لاحقاً)
                    login(request, user)
            else:
                login(request, user)

            # بعد تسجيل الدخول مباشرةً: اختيار مدرسة افتراضية عند توفر مدرسة واحدة فقط
            try:
                # إن كان للمستخدم مدرسة واحدة فقط ضمن عضوياته نعتبرها المدرسة النشطة
                schools = _user_schools(user)
                if len(schools) == 1:
                    _set_active_school(request, schools[0])
                # أو إن كان مشرفاً عاماً وهناك مدرسة واحدة فقط مفعّلة في النظام
                elif user.is_superuser:
                    qs = School.objects.filter(is_active=True)
                    if qs.count() == 1:
                        s = qs.first()
                        if s is not None:
                            _set_active_school(request, s)
            except Exception:
                pass

            next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
            # الوجهة الافتراضية حسب الدور
            if getattr(user, "is_superuser", False):
                default_name = "reports:platform_admin_dashboard"
            elif _is_staff(user):
                default_name = "reports:admin_dashboard"
            else:
                default_name = "reports:home"
            return redirect(next_url or default_name)
        messages.error(request, "رقم الجوال أو كلمة المرور غير صحيحة")

    context = {"next": _safe_next_url(request.GET.get("next"))}
    return render(request, "reports/login.html", context)


@require_http_methods(["GET"])
def platform_landing(request: HttpRequest) -> HttpResponse:
    """الصفحة الرئيسية العامة للمنصة (تعريف + مميزات + زر دخول).

    - المستخدِم المسجّل بالفعل يُعاد توجيهه مباشرةً للواجهة المناسبة.
    - الزر الأساسي يقود إلى شاشة تسجيل الدخول العادية.
    """

    if getattr(request.user, "is_authenticated", False):
        if getattr(request.user, "is_superuser", False):
            return redirect("reports:platform_admin_dashboard")
        if _is_staff(request.user):
            return redirect("reports:admin_dashboard")
        return redirect("reports:home")

    return render(request, "reports/landing.html", {})

@require_http_methods(["GET", "POST"])
def logout_view(request: HttpRequest) -> HttpResponse:
    if request.user.is_authenticated:
        logout(request)
        messages.success(request, "تم تسجيل الخروج بنجاح.")
    return redirect("reports:login")


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def switch_school(request: HttpRequest) -> HttpResponse:
    """تبديل المدرسة من الهيدر لأي مستخدم يملك عضوية في أكثر من مدرسة."""

    next_url = request.POST.get("next") or request.META.get("HTTP_REFERER") or "reports:home"
    sid = request.POST.get("school_id")
    try:
        if request.user.is_superuser:
            school = School.objects.get(pk=sid, is_active=True)
        else:
            allowed = _user_schools(request.user)
            school = next((s for s in allowed if str(s.pk) == str(sid)), None)
            if school is None:
                raise School.DoesNotExist
        _set_active_school(request, school)
        messages.success(request, f"تم التبديل إلى: {school.name}")

        # ✅ حماية الاشتراك: إذا كانت المدرسة المختارة اشتراكها منتهي/غير موجود
        # نوجّه مباشرةً لصفحة (انتهاء الاشتراك). المدير سيُسمح له فقط بصفحات التجديد.
        if not request.user.is_superuser:
            sub = None
            try:
                sub = school.subscription
            except Exception:
                sub = None
            try:
                expired = True if sub is None else bool(sub.is_expired)
            except Exception:
                expired = True
            if expired:
                return redirect("reports:subscription_expired")
    except Exception:
        messages.error(request, "تعذّر تبديل المدرسة. تأكد من الصلاحيات.")

    if isinstance(next_url, str) and not next_url.startswith("http"):
        return redirect(next_url)
    return redirect("reports:home")


@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET", "POST"])
def select_school(request: HttpRequest) -> HttpResponse:
    """شاشة اختيار المدرسة للآدمن ومديري المدارس.

    - المستخدم السوبر يوزر يشاهد جميع المدارس.
    - مدير المدرسة يشاهد فقط المدارس التي هو مدير لها.
    """

    if request.user.is_superuser:
        schools_qs = School.objects.filter(is_active=True).order_by("name")
    else:
        manager_schools = _user_manager_schools(request.user)
        schools_qs = School.objects.filter(id__in=[s.id for s in manager_schools], is_active=True).order_by("name")

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

    context = {
        "schools": list(schools_qs),
        "current_school": _get_active_school(request),
    }
    return render(request, "reports/select_school.html", context)

# =========================
# الرئيسية (لوحة المعلم)
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def home(request: HttpRequest) -> HttpResponse:
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
                "recent_reports": recent_reports[:2],
                "req_stats": req_stats,
                "recent_tickets": recent_tickets[:2],
                "home_notification": home_notification,
                "home_notification_recipient_id": home_notification_recipient_id,
            },
        )
    except Exception:
        logger.exception("Home view failed")
        if settings.DEBUG or os.getenv("SHOW_ERRORS") == "1":
            html = "<h2>Home exception</h2><pre>{}</pre>".format(traceback.format_exc())
            return HttpResponse(html, status=500)
    return redirect("reports:home")

# =========================
# التقارير: إضافة/عرض/إدارة
# =========================
@login_required(login_url="reports:login")
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

            teacher_name_input = (request.POST.get("teacher_name") or "").strip()
            teacher_name_final = teacher_name_input or (getattr(request.user, "name", "") or "").strip()
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
    qs = _filter_by_school(
        Report.objects.select_related("teacher", "category")
        .filter(teacher=request.user)
        .order_by("-report_date", "-id"),
        active_school,
    )
    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))
    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)

    page = request.GET.get("page", 1)
    paginator = Paginator(qs, 10)
    try:
        reports_page = paginator.page(page)
    except PageNotAnInteger:
        reports_page = paginator.page(1)
    except EmptyPage:
        reports_page = paginator.page(paginator.num_pages)

    return render(
        request,
        "reports/my_reports.html",
        {
            "reports": reports_page,
            "start_date": request.GET.get("start_date", ""),
            "end_date": request.GET.get("end_date", ""),
        },
    )

@user_passes_test(_is_staff, login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def admin_reports(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    cats = allowed_categories_for(request.user, active_school)
    qs = Report.objects.select_related("teacher", "category").order_by("-report_date", "-id")
    qs = restrict_queryset_for_user(qs, request.user, active_school)
    qs = _filter_by_school(qs, active_school)

    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))
    teacher_name = (request.GET.get("teacher_name") or "").strip()
    category = (request.GET.get("category") or "").strip().lower()

    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)
    if teacher_name:
        for t in [t for t in teacher_name.split() if t]:
            qs = qs.filter(teacher_name__icontains=t)

    if category:
        if cats and "all" not in cats:
            if category in cats:
                qs = qs.filter(category__code=category)
        else:
            qs = qs.filter(category__code=category)

    if HAS_RTYPE and ReportType is not None:
        rtypes_qs = ReportType.objects.filter(is_active=True).order_by("order", "name")
        if active_school is not None and hasattr(ReportType, "school"):
            rtypes_qs = rtypes_qs.filter(school=active_school)
        allowed_choices = [(rt.code, rt.name) for rt in rtypes_qs]
    else:
        allowed_choices = []

    page = request.GET.get("page", 1)
    paginator = Paginator(qs, 20)
    try:
        reports_page = paginator.page(page)
    except PageNotAnInteger:
        reports_page = paginator.page(1)
    except EmptyPage:
        reports_page = paginator.page(paginator.num_pages)

    context = {
        "reports": reports_page,
        "start_date": request.GET.get("start_date", ""),
        "end_date": request.GET.get("end_date", ""),
        "teacher_name": teacher_name,
        "category": category if (not cats or "all" in cats or category in cats) else "",
        "categories": allowed_choices,
    }
    return render(request, "reports/admin_reports.html", context)

# =========================
# لوحة تقارير المسؤول (Officer)
# =========================
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

    qs = Report.objects.select_related("teacher", "category").filter(category__in=allowed_cats_qs)
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
# حذف تقرير (لوحة المدير)
# =========================
@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["POST"])
def admin_delete_report(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    # في حال وجود مدارس مفعّلة يجب أن تكون هناك مدرسة مختارة للمدير
    if School.objects.filter(is_active=True).exists() and active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً قبل حذف التقارير.")
        return redirect("reports:select_school")

    qs = Report.objects.all()
    qs = _filter_by_school(qs, active_school)
    report = get_object_or_404(qs, pk=pk)
    report.delete()
    messages.success(request, "تم حذف التقرير بنجاح.")
    return _safe_redirect(request, "reports:admin_reports")

# =========================
# حذف تقرير (لوحة المسؤول Officer)
# =========================
@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["POST"])
def officer_delete_report(request: HttpRequest, pk: int) -> HttpResponse:
    # ✅ تأكيد أن المستخدم مسؤول داخل المدرسة النشطة قبل السماح بالحذف عبر هذا المسار
    active_school = _get_active_school(request)
    if not getattr(request.user, "is_staff", False):
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if DepartmentMembership is None:
            messages.error(request, "صلاحيات المسؤول تتطلب تفعيل الأقسام وعضوياتها.")
            return redirect("reports:home")
        has_officer_membership = DepartmentMembership.objects.filter(
            teacher=request.user,
            role_type=DM_OFFICER,
            department__is_active=True,
            department__school=active_school,
        ).exists()
        if not has_officer_membership:
            messages.error(request, "لا تملك صلاحية مسؤول قسم في هذه المدرسة.")
            return redirect("reports:home")

    try:
        r = _get_report_for_user_or_404(request, pk)  # 404 تلقائيًا خارج النطاق ومع عزل المدرسة
        r.delete()
        messages.success(request, "🗑️ تم حذف التقرير بنجاح.")
    except Exception:
        messages.error(request, "تعذّر حذف التقرير أو لا تملك صلاحية لذلك.")
    return _safe_redirect(request, "reports:officer_reports")

# =========================
# الوصول إلى تقرير معيّن (مع احترام المدرسة النشطة)
# =========================
def _get_report_for_user_or_404(request: HttpRequest, pk: int):
    user = request.user
    qs = Report.objects.select_related("teacher", "category")

    # عزل حسب المدرسة النشطة إن كان للموديل حقل school
    active_school = _get_active_school(request)
    if active_school is not None:
        try:
            if "school" in [f.name for f in Report._meta.get_fields()]:
                qs = qs.filter(school=active_school)
        except Exception:
            pass

    if getattr(user, "is_staff", False):
        return get_object_or_404(qs, pk=pk)

    try:
        cats = allowed_categories_for(user, active_school) or set()
    except Exception:
        cats = set()

    if "all" in cats:
        return get_object_or_404(qs, pk=pk)

    if cats:
        return get_object_or_404(
            qs.filter(Q(teacher=user) | Q(category__code__in=list(cats))),
            pk=pk,
        )

    return get_object_or_404(qs, pk=pk, teacher=user)

# =========================
# طباعة التقرير (نسخة مُحسّنة)
# =========================
def _resolve_department_for_category(cat):
    """يستخرج كائن القسم المرتبط بالتصنيف (إن وُجد)."""
    if not cat or Department is None:
        return None

    # 1) علاقة مباشرة cat.department (إن وُجدت)
    try:
        d = getattr(cat, "department", None)
        if d:
            return d
    except Exception:
        pass

    # 2) علاقات M2M شائعة: departments / depts / dept_list
    for rel_name in ("departments", "depts", "dept_list"):
        rel = getattr(cat, rel_name, None)
        if rel is not None:
            try:
                d = rel.all().first()
                if d:
                    return d
            except Exception:
                pass

    # 3) استعلام احتياطي
    try:
        return Department.objects.filter(reporttypes=cat).first()
    except Exception:
        return None

def _build_head_decision(dept):
    """
    يُرجع dict يحدّد ماذا نطبع في خانة (اعتماد رئيس القسم).
    - بدون قسم: لا نعرض شيئًا.
    - رئيس واحد: نعرض اسمه.
    - أكثر من رئيس: حسب السياسة PRINT_MULTIHEAD_POLICY = "blank" أو "dept".
    """
    if not dept or DepartmentMembership is None:
        return {"no_render": True}

    try:
        role_officer = getattr(DepartmentMembership, "OFFICER", "officer")
        qs = (DepartmentMembership.objects
              .select_related("teacher")
              .filter(department=dept, role_type=role_officer, teacher__is_active=True))
        heads = [m.teacher for m in qs]
    except Exception:
        heads = []

    count = len(heads)
    policy = getattr(settings, "PRINT_MULTIHEAD_POLICY", "blank")  # "blank" أو "dept"

    if count == 1:
        return {"single": True, "name": getattr(heads[0], "name", str(heads[0]))}

    if policy == "dept":
        return {"multi_dept": True, "dept_name": getattr(dept, "name", "")}

    return {"multi_blank": True}

@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def report_print(request: HttpRequest, pk: int) -> HttpResponse:
    r = _get_report_for_user_or_404(request, pk)

    active_school = _get_active_school(request)
    school_scope = getattr(r, "school", None) or active_school

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
        dept = _resolve_department_for_category(cat)
        # حماية إضافية: تأكد أن القسم من نفس مدرسة التقرير/المدرسة النشطة
        if dept is not None and school_scope is not None:
            try:
                dept_school = getattr(dept, "school", None)
                if dept_school is not None and dept_school != school_scope:
                    dept = None
            except Exception:
                dept = None

    head_decision = _build_head_decision(dept)

    # اسم مدير المدرسة: نبحث عن مدير مفعّل للمدرسة المرتبطة بالتقرير،
    # أو نستخدم المدرسة النشطة كاحتياط، ثم نرجع لإعدادات النظام إذا لم نجد.
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

    # لون قالب الطباعة: من إعدادات المدرسة إن وُجد، وإلا من إعدادات النظام أو قيمة افتراضية
    print_color = "#2563eb"
    try:
        school_for_color = getattr(r, "school", None) or _get_active_school(request)
        if school_for_color is not None:
            color_val = getattr(school_for_color, "print_primary_color", "") or ""
            if color_val:
                print_color = color_val
        else:
            print_color = getattr(settings, "SCHOOL_PRINT_COLOR", print_color)
    except Exception:
        print_color = getattr(settings, "SCHOOL_PRINT_COLOR", print_color)

    return render(
        request,
        "reports/report_print.html",
        {
            "r": r,
            "head_decision": head_decision,   # ← القالب يعتمد عليه
            "SCHOOL_PRINCIPAL": school_principal,
            "PRINT_PRIMARY_COLOR": print_color,
        },
    )

@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def report_pdf(request: HttpRequest, pk: int) -> HttpResponse:
    try:
        from weasyprint import CSS, HTML
    except Exception:
        return HttpResponse("WeasyPrint غير مثبت. ثبّت الحزمة وشغّل مجددًا.", status=500)

    r = _get_report_for_user_or_404(request, pk)

    active_school = _get_active_school(request)
    school_scope = getattr(r, "school", None) or active_school

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
        dept = _resolve_department_for_category(cat)
        if dept is not None and school_scope is not None:
            try:
                dept_school = getattr(dept, "school", None)
                if dept_school is not None and dept_school != school_scope:
                    dept = None
            except Exception:
                dept = None

    head_decision = _build_head_decision(dept)

    # إعادة استخدام نفس منطق اسم مدير المدرسة لضمان تطابق الطباعة و PDF
    school_principal = ""
    try:
        school_for_principal = getattr(r, "school", None)
        if school_for_principal is None:
            # لا توجد request في _get_active_school هنا بسهولة، لكن في PDF نفضل مدرسة التقرير نفسها فقط
            school_for_principal = getattr(r, "school", None)
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

    # نفس منطق لون قالب الطباعة كما في report_print
    print_color = "#2563eb"
    try:
        school_for_color = getattr(r, "school", None)
        if school_for_color is not None:
            color_val = getattr(school_for_color, "print_primary_color", "") or ""
            if color_val:
                print_color = color_val
        else:
            print_color = getattr(settings, "SCHOOL_PRINT_COLOR", print_color)
    except Exception:
        print_color = getattr(settings, "SCHOOL_PRINT_COLOR", print_color)

    html = render_to_string(
        "reports/report_print.html",
        {
            "r": r,
            "for_pdf": True,
            "head_decision": head_decision,
            "SCHOOL_PRINCIPAL": school_principal,
            "PRINT_PRIMARY_COLOR": print_color,
        },
        request=request,
    )
    css = CSS(string="@page { size: A4; margin: 14mm 12mm; }")
    pdf = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf(stylesheets=[css])

    resp = HttpResponse(pdf, content_type="application/pdf")
    resp["Content-Disposition"] = f'inline; filename="report-{r.pk}.pdf"'
    return resp

# =========================
# إدارة المعلّمين (مدير فقط)
# =========================
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def manage_teachers(request: HttpRequest) -> HttpResponse:
    # يَعرض المدير فقط المعلّمين المرتبطين بمدرسته النشطة
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    term = (request.GET.get("q") or "").strip()

    if Department is not None:
        dept_name_sq = Department.objects.filter(slug=OuterRef("role__slug")).values("name")[:1]
        qs = Teacher.objects.select_related("role").annotate(role_dept_name=Subquery(dept_name_sq)).order_by("-id")
    else:
        qs = Teacher.objects.select_related("role").order_by("-id")

    # حصر المعلمين على عضويات المدرسة الحالية فقط
    if active_school is not None:
        qs = qs.filter(
            school_memberships__school=active_school,
            school_memberships__is_active=True,
            school_memberships__role_type=SchoolMembership.RoleType.TEACHER,
        ).distinct()

    if term:
        qs = qs.filter(Q(name__icontains=term) | Q(phone__icontains=term) | Q(national_id__icontains=term))

    page = Paginator(qs, 20).get_page(request.GET.get("page"))
    return render(request, "reports/manage_teachers.html", {"teachers_page": page, "term": term})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def add_teacher(request: HttpRequest) -> HttpResponse:
    # كل معلم جديد يُربط تلقائياً بالمدرسة النشطة لهذا المدير
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    if request.method == "POST":
        # إنشاء معلّم فقط: بدون قسم/بدون دور داخل قسم. التكاليف تتم من صفحة أعضاء القسم.
        form = TeacherCreateForm(request.POST)
        if form.is_valid():
            try:
                with transaction.atomic():
                    teacher = form.save(commit=True)
                    # ربط المعلّم بالمدرسة الحالية كـ TEACHER
                    if active_school is not None:
                        SchoolMembership.objects.update_or_create(
                            school=active_school,
                            teacher=teacher,
                            role_type=SchoolMembership.RoleType.TEACHER,
                            defaults={"is_active": True},
                        )
                messages.success(request, "✅ تم إضافة المستخدم بنجاح.")
                next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
                return redirect(next_url or "reports:manage_teachers")
            except IntegrityError:
                messages.error(request, "تعذّر الحفظ: قد يكون رقم الجوال أو الهوية مستخدمًا مسبقًا.")
            except Exception:
                logger.exception("add_teacher failed")
                messages.error(request, "حدث خطأ غير متوقع أثناء الحفظ. جرّب لاحقًا.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء الظاهرة.")
    else:
        form = TeacherCreateForm()
    return render(request, "reports/add_teacher.html", {"form": form, "title": "إضافة مستخدم"})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def edit_teacher(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    teacher = get_object_or_404(Teacher, pk=pk)

    # لا يُسمح للمدير بتعديل معلّم غير مرتبط بمدرسته
    if not getattr(request.user, "is_superuser", False) and active_school is not None:
        has_membership = SchoolMembership.objects.filter(
            school=active_school,
            teacher=teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
            is_active=True,
        ).exists()
        if not has_membership:
            messages.error(request, "لا يمكنك تعديل هذا المعلّم لأنه غير مرتبط بمدرستك.")
            return redirect("reports:manage_teachers")
    if request.method == "POST":
        # تعديل بيانات المعلّم فقط — التكاليف تتم من صفحة أعضاء القسم
        form = TeacherEditForm(request.POST, instance=teacher)
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save(commit=True)
                messages.success(request, "✏️ تم تحديث بيانات المستخدم بنجاح.")
                return redirect("reports:manage_teachers")
            except Exception:
                logger.exception("edit_teacher failed")
                messages.error(request, "حدث خطأ غير متوقع أثناء التحديث.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء الظاهرة.")
    else:
        form = TeacherEditForm(instance=teacher)

    return render(request, "reports/edit_teacher.html", {"form": form, "teacher": teacher, "title": "تعديل مستخدم"})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def delete_teacher(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    teacher = get_object_or_404(Teacher, pk=pk)

    # لا يُسمح للمدير بحذف معلّم غير مرتبط بمدرسته
    if not getattr(request.user, "is_superuser", False) and active_school is not None:
        has_membership = SchoolMembership.objects.filter(
            school=active_school,
            teacher=teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
            is_active=True,
        ).exists()
        if not has_membership:
            messages.error(request, "لا يمكنك حذف هذا المعلّم لأنه غير مرتبط بمدرستك.")
            return redirect("reports:manage_teachers")
    try:
        with transaction.atomic():
            teacher.delete()
        messages.success(request, "🗑️ تم حذف المستخدم.")
    except Exception:
        logger.exception("delete_teacher failed")
        messages.error(request, "تعذّر حذف المستخدم. حاول لاحقًا.")
    next_url = _safe_next_url(request.POST.get("next") or request.GET.get("next"))
    return redirect(next_url or "reports:manage_teachers")

# =========================
# التذاكر (Tickets)
# =========================
def _can_act(user, ticket: Ticket) -> bool:
    if not getattr(user, "is_authenticated", False):
        return False

    # 1. المشرف العام (تذاكر المنصة)
    if ticket.is_platform and getattr(user, "is_superuser", False):
        return True

    # 2. المستلم المباشر (Assignee)
    if ticket.assignee_id == user.id:
        return True

    # 3. مدير المدرسة (لتذاكر المدرسة)
    # يحق للمدير التحكم في أي تذكرة تابعة لمدرسته
    if not ticket.is_platform and ticket.school_id:
        if SchoolMembership.objects.filter(
            school_id=ticket.school_id,
            teacher=user,
            role_type=SchoolMembership.RoleType.MANAGER,
            is_active=True
        ).exists():
            return True

    # 4. مسؤول القسم (Officer)
    # إذا كانت التذكرة تابعة لقسم، فمسؤول القسم يملك صلاحية عليها
    if ticket.department_id and DepartmentMembership is not None:
        if DepartmentMembership.objects.filter(
            department_id=ticket.department_id,
            teacher=user,
            role_type=DepartmentMembership.OFFICER
        ).exists():
            # عزل المدرسة: لمسؤول القسم، لا نسمح بالتعامل مع تذكرة مدرسة أخرى
            try:
                if (not ticket.is_platform) and ticket.school_id:
                    if not SchoolMembership.objects.filter(
                        teacher=user,
                        school_id=ticket.school_id,
                        is_active=True,
                    ).exists():
                        return False
            except Exception:
                pass
            return True

    return False

@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def request_create(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)

    # إذا كانت هناك مدارس مفعّلة، نلزم اختيار مدرسة لإنشاء تذكرة مدرسة
    if School.objects.filter(is_active=True).exists() and active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    if request.method == "POST":
        form = TicketCreateForm(request.POST, request.FILES, user=request.user, active_school=active_school)
        if form.is_valid():
            ticket: Ticket = form.save(commit=True, user=request.user)  # يحفظ التذكرة والصور
            if hasattr(ticket, "school") and active_school is not None:
                ticket.school = active_school
                ticket.save(update_fields=["school"])
            messages.success(request, "✅ تم إرسال الطلب بنجاح.")
            return redirect("reports:my_requests")
        messages.error(request, "فضلاً تحقّق من الحقول.")
    else:
        form = TicketCreateForm(user=request.user, active_school=active_school)
    return render(request, "reports/request_create.html", {"form": form})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def support_ticket_create(request: HttpRequest) -> HttpResponse:
    """إنشاء تذكرة دعم فني للمنصة (للمدراء فقط)"""
    from .forms import SupportTicketForm
    
    active_school = _get_active_school(request)

    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")
    
    if request.method == "POST":
        form = SupportTicketForm(request.POST, request.FILES)
        if form.is_valid():
            ticket = form.save(commit=False, user=request.user)
            if active_school:
                ticket.school = active_school
            ticket.save()
            messages.success(request, "✅ تم إرسال طلب الدعم الفني بنجاح.")
            return redirect("reports:my_support_tickets")
        messages.error(request, "فضلاً تحقّق من الحقول.")
    else:
        form = SupportTicketForm()
        
    return render(request, "reports/support_ticket_create.html", {"form": form})


@login_required(login_url="reports:login")
@role_required({"manager"})
def my_support_tickets(request: HttpRequest) -> HttpResponse:
    """عرض تذاكر الدعم الفني الخاصة بالمدير"""
    active_school = _get_active_school(request)

    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    tickets = Ticket.objects.filter(
        creator=request.user, 
        is_platform=True,
        school=active_school,
    ).order_by("-created_at")
    
    return render(request, "reports/my_support_tickets.html", {"tickets": tickets})


@login_required(login_url="reports:login")
def my_requests(request: HttpRequest) -> HttpResponse:
    user = request.user
    active_school = _get_active_school(request)

    if School.objects.filter(is_active=True).exists() and active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    notes_qs = (
        TicketNote.objects.filter(is_public=True)
        .select_related("author")
        .only("id", "body", "created_at", "author__name")
        .order_by("-created_at", "-id")
    )
    base_qs = _filter_by_school(
        Ticket.objects.select_related("assignee", "department")
        .prefetch_related(Prefetch("notes", queryset=notes_qs, to_attr="pub_notes"))
        .only("id", "title", "status", "department", "created_at", "assignee__name")
        .filter(creator=user, is_platform=False),
        active_school,
    )

    q = (request.GET.get("q") or "").strip()
    if q:
        base_qs = base_qs.filter(Q(title__icontains=q) | Q(id__icontains=q) | Q(assignee__name__icontains=q))

    counts = dict(base_qs.values("status").annotate(c=Count("id")).values_list("status", "c"))
    stats = {
        "open": counts.get("open", 0),
        "in_progress": counts.get("in_progress", 0),
        "done": counts.get("done", 0),
        "rejected": counts.get("rejected", 0),
    }

    status = request.GET.get("status")
    qs = base_qs
    if status in {"open", "in_progress", "done", "rejected"}:
        qs = qs.filter(status=status)

    order = request.GET.get("order") or "-created_at"
    allowed_order = {"-created_at", "created_at", "-id", "id"}
    if order not in allowed_order:
        order = "-created_at"
    if order in {"created_at", "-created_at"}:
        qs = qs.order_by(order, "-id")
    else:
        qs = qs.order_by(order)

    page = Paginator(qs, 12).get_page(request.GET.get("page") or 1)
    view_mode = request.GET.get("view", "list")

    return render(
        request,
        "reports/my_requests.html",
        {"tickets": page, "page_obj": page, "stats": stats, "view_mode": view_mode},
    )

@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def ticket_detail(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)

    # احضر التذكرة مع الحقول المطلوبة مع احترام المدرسة النشطة
    base_qs = Ticket.objects.select_related("creator", "assignee", "department").only(
        "id", "title", "body", "status", "department", "created_at",
        "creator__name", "assignee__name", "assignee_id", "creator_id", "is_platform", "school_id"
    )
    
    # إذا كانت التذكرة للمنصة، لا نفلتر بالمدرسة (لأنها قد لا تكون مرتبطة بمدرسة أو نريد السماح للمدير برؤيتها)
    # لكن يجب التأكد أن المستخدم هو المنشئ أو مشرف نظام
    # سنحاول جلب التذكرة أولاً بدون فلتر المدرسة إذا كانت is_platform=True
    
    # الحل الأبسط: نعدل _filter_by_school ليتجاهل الفلتر إذا كانت التذكرة is_platform=True
    # لكن _filter_by_school تعمل على QuerySet.
    
    # لذا سنقوم بالتالي:
    # 1. نحاول جلب التذكرة بـ PK فقط
    # 2. نتحقق من الصلاحية يدوياً
    
    t = get_object_or_404(base_qs, pk=pk)
    
    # التحقق من الوصول
    if t.is_platform:
        # تذاكر المنصة: مسموحة للمنشئ (المدير) أو المشرف العام
        if not (request.user.is_superuser or t.creator_id == request.user.id):
             raise Http404("ليس لديك صلاحية لعرض هذه التذكرة.")
    else:
        # تذاكر المدرسة: نلزم عضوية المستخدم في مدرسة التذكرة
        if not request.user.is_superuser:
            if not t.school_id:
                raise Http404("هذه التذكرة غير مرتبطة بمدرسة.")
            if not SchoolMembership.objects.filter(
                teacher=request.user,
                school_id=t.school_id,
                is_active=True,
            ).exists():
                raise Http404("ليس لديك صلاحية لعرض هذه التذكرة.")

            # عند تعدد المدارس: نلزم توافق المدرسة النشطة مع مدرسة التذكرة
            if active_school is not None and t.school_id != active_school.id:
                raise Http404("هذه التذكرة تابعة لمدرسة أخرى.")

    is_owner = (t.creator_id == request.user.id)
    can_act = _can_act(request.user, t)

    if request.method == "POST":
        status_val = (request.POST.get("status") or "").strip()
        note_txt   = (request.POST.get("note") or "").strip()
        changed = False

        # إضافة ملاحظة (المرسل أو من يملك الصلاحية)
        # يسمح للمرسل بإضافة ملاحظات (للتواصل) ولكن لا يملك صلاحية تغيير الحالة إلا إذا كان من ضمن المستلمين/الإدارة
        can_comment = False
        if is_owner or can_act:
            can_comment = True

        if note_txt and can_comment:
            try:
                with transaction.atomic():
                    TicketNote.objects.create(
                        ticket=t, author=request.user, body=note_txt, is_public=True
                    )

                    # خيار: إعادة الفتح تلقائيًا عند ملاحظة المرسل (إن كانت مفعّلة)
                    if AUTO_REOPEN_ON_SENDER_NOTE and is_owner and t.status in {
                        Ticket.Status.DONE, Ticket.Status.REJECTED, Ticket.Status.IN_PROGRESS
                    }:
                        old_status = t.status
                        t.status = Ticket.Status.OPEN
                        try:
                            t.save(update_fields=["status"])
                        except Exception:
                            t.save()
                        TicketNote.objects.create(
                            ticket=t,
                            author=request.user,
                            body=f"تغيير الحالة تلقائيًا بسبب ملاحظة المرسل: {old_status} → {Ticket.Status.OPEN}",
                            is_public=True,
                        )
                    changed = True
            except Exception:
                logger.exception("Failed to create note")
                messages.error(request, "تعذّر حفظ الملاحظة.")

        # تغيير الحالة (لمن له صلاحية فقط)
        if status_val:
            if not can_act:
                messages.warning(request, "لا يمكنك تغيير حالة هذا الطلب. يمكنك فقط إضافة ملاحظة.")
            else:
                valid_statuses = {k for k, _ in Ticket.Status.choices}
                if status_val in valid_statuses and status_val != t.status:
                    old = t.status
                    t.status = status_val
                    try:
                        t.save(update_fields=["status"])
                    except Exception:
                        t.save()
                    changed = True
                    try:
                        TicketNote.objects.create(
                            ticket=t,
                            author=request.user,
                            body="تغيير الحالة: {} → {}".format(old, status_val),
                            is_public=True,
                        )
                    except Exception:
                        logger.exception("Failed to create system note")

        if changed:
            messages.success(request, "تم حفظ التغييرات.")
        else:
            messages.info(request, "لا يوجد تغييرات.")
        return redirect("reports:ticket_detail", pk=pk)

    # ===== صور التذكرة (بغض النظر عن related_name) =====
    images_manager = getattr(t, "images", None)  # لو related_name='images'
    if images_manager is None:
        images_manager = getattr(t, "ticketimage_set", None)  # الاسم الافتراضي إن وُجد

    if images_manager is not None and hasattr(images_manager, "all"):
        images = list(images_manager.all().only("id", "image"))
    else:
        # fallback مضمون
        images = list(TicketImage.objects.filter(ticket_id=t.id).only("id", "image"))

    # سجلّ الملاحظات + نموذج الإجراء (إن وُجدت صلاحية)
    notes_qs = (
        t.notes.select_related("author")
        .only("id", "body", "created_at", "author__name")
        .order_by("-created_at", "-id")
    )
    form = TicketActionForm(initial={"status": t.status}) if can_act else None

    ctx = {
        "t": t,
        "images": images,     # ← استخدم هذا في القالب
        "notes": notes_qs,
        "form": form,
        "can_act": can_act,
        "is_owner": is_owner,
    }
    return render(request, "reports/ticket_detail.html", ctx)

@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET", "POST"])
def admin_request_update(request: HttpRequest, pk: int) -> HttpResponse:
    return ticket_detail(request, pk)

# ========= دعم الأقسام =========
def _dept_code_for(dept_obj_or_code) -> str:
    if hasattr(dept_obj_or_code, "slug") and getattr(dept_obj_or_code, "slug"):
        return getattr(dept_obj_or_code, "slug")
    if hasattr(dept_obj_or_code, "code") and getattr(dept_obj_or_code, "code"):
        return getattr(dept_obj_or_code, "code")
    return str(dept_obj_or_code or "").strip()

def _arabic_label_for(dept_obj_or_code) -> str:
    if hasattr(dept_obj_or_code, "name") and getattr(dept_obj_or_code, "name"):
        return dept_obj_or_code.name
    code = (
        getattr(dept_obj_or_code, "slug", None)
        or getattr(dept_obj_or_code, "code", None)
        or (dept_obj_or_code if isinstance(dept_obj_or_code, str) else "")
    )
    return _role_display_map().get(code, code or "—")

def _resolve_department_by_code_or_pk(code_or_pk: str, school: Optional[School] = None) -> Tuple[Optional[object], str, str]:
    dept_obj = None
    dept_code = (code_or_pk or "").strip()

    if Department is not None:
        try:
            qs = Department.objects.all()
            if school is not None and hasattr(Department, "school"):
                qs = qs.filter(school=school)
            dept_obj = qs.filter(slug__iexact=dept_code).first()
            if not dept_obj:
                try:
                    dept_obj = qs.filter(pk=int(dept_code)).first()
                except (ValueError, TypeError):
                    dept_obj = None
        except Exception:
            dept_obj = None

        if dept_obj:
            dept_code = getattr(dept_obj, "slug", dept_code)

    dept_label = _arabic_label_for(dept_obj or dept_code)
    return dept_obj, dept_code, dept_label

def _members_for_department(dept_code: str, school: Optional[School] = None):
    if not dept_code:
        return Teacher.objects.none()
    if DepartmentMembership is None:
        return Teacher.objects.none()

    mem_qs = DepartmentMembership.objects.filter(department__slug__iexact=dept_code)
    if school is not None:
        mem_qs = mem_qs.filter(department__school=school)
    member_ids = mem_qs.values_list("teacher_id", flat=True)

    qs = Teacher.objects.filter(is_active=True, id__in=member_ids).distinct()
    if school is not None:
        qs = qs.filter(
            school_memberships__school=school,
            school_memberships__is_active=True,
        )
    return qs.order_by("name")

def _user_department_codes(user) -> list[str]:
    codes = set()
    if DepartmentMembership is not None:
        try:
            mem_codes = (
                DepartmentMembership.objects.filter(teacher=user)
                .values_list("department__slug", flat=True)
            )
            for c in mem_codes:
                if c:
                    codes.add(c)
        except Exception:
            logger.exception("Failed to fetch user department codes")

    return list(codes)

def _tickets_stats_for_department(dept_code: str, school: Optional[School] = None) -> dict:
    qs = Ticket.objects.filter(department__slug=dept_code)
    qs = _filter_by_school(qs, school)
    return {
        "open": qs.filter(status="open").count(),
        "in_progress": qs.filter(status="in_progress").count(),
        "done": qs.filter(status="done").count(),
    }

def _all_departments(active_school: Optional[School] = None):
    items = []
    if Department is not None:
        qs = Department.objects.all().order_by("id")
        if active_school is not None and hasattr(Department, "school"):
            qs = qs.filter(school=active_school)
        for d in qs:
            code = _dept_code_for(d)
            stats = _tickets_stats_for_department(code, active_school)
            role_ids = set(Teacher.objects.filter(role__slug=code, is_active=True).values_list("id", flat=True))
            member_ids = set()
            if DepartmentMembership is not None:
                member_ids = set(DepartmentMembership.objects.filter(department=d).values_list("teacher_id", flat=True))
            members_count = len(role_ids | member_ids)
            items.append(
                {
                    "pk": d.pk,
                    "code": code,
                    "name": _arabic_label_for(d),
                    "is_active": getattr(d, "is_active", True),
                    "members_count": members_count,
                    "stats": stats,
                }
            )
    else:
        items = []
    return items

class _DepartmentForm(forms.ModelForm):
    class Meta:
        model = Department
        fields: list[str] = []
        if model is not None:
            for fname in ("name", "slug", "role_label", "is_active"):
                if hasattr(model, fname):
                    fields.append(fname)

    def clean(self):
        cleaned = super().clean()
        return cleaned

def get_department_form():
    if Department is not None and 'DepartmentForm' in globals() and (DepartmentForm is not None):
        return DepartmentForm
    if Department is not None:
        return _DepartmentForm
    return None


# ---- إعدادات المدرسة الحالية (للمدير أو المشرف العام) ----
class _SchoolSettingsForm(forms.ModelForm):
    class Meta:
        model = School
        fields = [
            "name",
            "stage",
            "gender",
            "city",
            "phone",
            "logo_url",
            "logo_file",
            "print_primary_color",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # حقل لون قالب الطباعة كـ color-picker في المتصفح
        if "print_primary_color" in self.fields:
            self.fields["print_primary_color"].widget = forms.TextInput(
                attrs={"type": "color"}
            )


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_settings(request: HttpRequest) -> HttpResponse:
    """إعدادات المدرسة الحالية (الاسم، الشعار...).

    - متاحة لمدير المدرسة على مدرسته النشطة فقط.
    - متاحة للمشرف العام على أي مدرسة بعد اختيارها كـ active_school.
    """
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    # تحقق من الصلاحيات
    if not (getattr(request.user, "is_superuser", False) or active_school in _user_manager_schools(request.user)):
        messages.error(request, "لا تملك صلاحية تعديل إعدادات هذه المدرسة.")
        return redirect("reports:admin_dashboard")

    form = _SchoolSettingsForm(request.POST or None, request.FILES or None, instance=active_school)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "تم تحديث إعدادات المدرسة بنجاح.")
            return redirect("reports:admin_dashboard")
        # في حال وجود أخطاء نعرضها للمستخدم ليسهل معرفة سبب الفشل
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
        try:
            for field, errors in form.errors.items():
                label = form.fields.get(field).label if field in form.fields else field
                joined = "; ".join(errors)
                messages.error(request, f"{label}: {joined}")
        except Exception:
            # لا نكسر الصفحة إن حدث خطأ أثناء بناء الرسالة
            pass

    return render(request, "reports/school_settings.html", {"form": form, "school": active_school})


# ---- إدارة المدارس (إنشاء/تعديل/حذف) للمشرف العام ----
class _SchoolAdminForm(forms.ModelForm):
    class Meta:
        model = School
        fields = [
            "name",
            "code",
            "stage",
            "gender",
            "city",
            "phone",
            "is_active",
            "logo_url",
            "logo_file",
            "print_primary_color",
        ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if "print_primary_color" in self.fields:
            self.fields["print_primary_color"].widget = forms.TextInput(
                attrs={"type": "color"}
            )


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_create(request: HttpRequest) -> HttpResponse:
    form = _SchoolAdminForm(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "تم إنشاء المدرسة بنجاح.")
            return redirect("reports:schools_admin_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/school_form.html", {"form": form, "mode": "create"})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_update(request: HttpRequest, pk: int) -> HttpResponse:
    school = get_object_or_404(School, pk=pk)
    form = _SchoolAdminForm(request.POST or None, instance=school)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "تم تحديث بيانات المدرسة.")
            return redirect("reports:schools_admin_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/school_form.html", {"form": form, "mode": "edit", "school": school})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["POST"])
def school_delete(request: HttpRequest, pk: int) -> HttpResponse:
    school = get_object_or_404(School, pk=pk)
    name = school.name
    try:
        school.delete()
        messages.success(request, f"🗑️ تم حذف المدرسة «{name}» وكل بياناتها المرتبطة.")
    except Exception:
        logger.exception("school_delete failed")
        messages.error(request, "تعذّر حذف المدرسة. ربما توجد قيود على البيانات المرتبطة.")
    return redirect("reports:schools_admin_list")


# ---- لوحة إدارة المدارس ومدراء المدارس (للسوبر أدمن) ----
@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET"])
def schools_admin_list(request: HttpRequest) -> HttpResponse:
    schools = (
        School.objects.all()
        .order_by("name")
        .prefetch_related(
            Prefetch(
                "memberships",
                queryset=SchoolMembership.objects.select_related("teacher").filter(
                    role_type=SchoolMembership.RoleType.MANAGER,
                    is_active=True,
                ),
                to_attr="manager_memberships",
            )
        )
    )

    items = []
    for s in schools:
        managers = [m.teacher for m in getattr(s, "manager_memberships", []) if m.teacher]
        items.append({"school": s, "managers": managers})

    return render(request, "reports/schools_admin_list.html", {"schools": items})


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def school_profile(request: HttpRequest, pk: int) -> HttpResponse:
    """بروفايل تفصيلي لمدرسة واحدة.

    - السوبر أدمن يمكنه عرض أي مدرسة.
    - مدير المدرسة يمكنه عرض المدارس التي يديرها فقط.
    """
    school = get_object_or_404(School, pk=pk)

    user = request.user
    allowed = False
    if getattr(user, "is_superuser", False):
        allowed = True
    elif _is_staff(user) and school in _user_manager_schools(user):
        allowed = True

    if not allowed:
        messages.error(request, "لا تملك صلاحية عرض هذه المدرسة.")
        return redirect("reports:admin_dashboard")

    # إحصائيات بسيطة للمدرسة
    reports_count = Report.objects.filter(school=school).count()

    tickets_qs = Ticket.objects.filter(school=school)
    tickets_total = tickets_qs.count()
    tickets_open = tickets_qs.filter(status__in=["open", "in_progress"]).count()
    tickets_done = tickets_qs.filter(status="done").count()
    tickets_rejected = tickets_qs.filter(status="rejected").count()

    teachers_qs = (
        Teacher.objects.filter(
            school_memberships__school=school,
            school_memberships__is_active=True,
        )
        .distinct()
        .order_by("name")
    )
    teachers_count = teachers_qs.count()

    departments_count = 0
    departments = []
    if Department is not None:
        try:
            depts_qs = Department.objects.filter(is_active=True)
            if DepartmentMembership is not None:
                depts_qs = (
                    depts_qs.filter(
                        memberships__teacher__school_memberships__school=school,
                        memberships__teacher__school_memberships__is_active=True,
                    )
                    .distinct()
                    .order_by("name")
                )
            departments_count = depts_qs.count()
            departments = list(depts_qs[:20])  # عرض عينات محدودة في القالب إن لزم
        except Exception:
            logger.exception("school_profile departments stats failed")

    context = {
        "school": school,
        "reports_count": reports_count,
        "tickets_total": tickets_total,
        "tickets_open": tickets_open,
        "tickets_done": tickets_done,
        "tickets_rejected": tickets_rejected,
        "teachers_count": teachers_count,
        "teachers": list(teachers_qs[:20]),  # أقصى 20 للعرض السريع
        "departments_count": departments_count,
        "departments": departments,
    }
    return render(request, "reports/school_profile.html", context)


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_managers_manage(request: HttpRequest, pk: int) -> HttpResponse:
    school = get_object_or_404(School, pk=pk)

    if request.method == "POST":
        action = request.POST.get("action")
        teacher_id = request.POST.get("teacher_id")
        if not teacher_id:
            messages.error(request, "الرجاء اختيار معلّم.")
            return redirect("reports:school_managers_manage", pk=school.pk)
        try:
            teacher = Teacher.objects.get(pk=teacher_id)
        except Teacher.DoesNotExist:
            messages.error(request, "المعلّم غير موجود.")
            return redirect("reports:school_managers_manage", pk=school.pk)

        if action == "add":
            # لا نسمح بأكثر من مدير نشط واحد لكل مدرسة
            other_manager_exists = SchoolMembership.objects.filter(
                school=school,
                role_type=SchoolMembership.RoleType.MANAGER,
                is_active=True,
            ).exclude(teacher=teacher).exists()
            if other_manager_exists:
                messages.error(request, "لا يمكن تعيين أكثر من مدير نشط لنفس المدرسة. قم بإلغاء تعيين المدير الحالي أولاً.")
                return redirect("reports:school_managers_manage", pk=school.pk)

            SchoolMembership.objects.update_or_create(
                school=school,
                teacher=teacher,
                role_type=SchoolMembership.RoleType.MANAGER,
                defaults={"is_active": True},
            )
            messages.success(request, f"تم تعيين {teacher.name} مديراً للمدرسة.")
        elif action == "remove":
            SchoolMembership.objects.filter(
                school=school,
                teacher=teacher,
                role_type=SchoolMembership.RoleType.MANAGER,
            ).update(is_active=False)
            messages.success(request, f"تم إلغاء إدارة {teacher.name} لهذه المدرسة.")
        else:
            messages.error(request, "إجراء غير معروف.")

        return redirect("reports:school_managers_manage", pk=school.pk)

    managers = (
        Teacher.objects.filter(
            school_memberships__school=school,
            school_memberships__role_type=SchoolMembership.RoleType.MANAGER,
            school_memberships__is_active=True,
        )
        .distinct()
        .order_by("name")
    )

    # في قائمة الإضافة نظهر فقط المستخدمين الذين هم "مديرون" على مستوى النظام
    # (إما أن يكون لهم الدور manager أو لديهم أي عضوية SchoolMembership كمدير).
    teachers = (
        Teacher.objects.filter(is_active=True)
        .filter(
            Q(role__slug__iexact=MANAGER_SLUG)
            | Q(
                school_memberships__role_type=SchoolMembership.RoleType.MANAGER,
                school_memberships__is_active=True,
            )
        )
        .distinct()
        .order_by("name")
    )

    context = {
        "school": school,
        "managers": managers,
        "teachers": teachers,
    }
    return render(request, "reports/school_managers_manage.html", context)

# ---- لوحة المدير المجمعة ----
@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@role_required({"manager"})
def admin_dashboard(request: HttpRequest) -> HttpResponse:
    # إذا لم يكن هناك مدرسة مختارة نوجّه لاختيار مدرسة أولاً
    active_school = _get_active_school(request)
    # السوبر يوزر يمكنه رؤية أي مدرسة، المدير مقيد بمدارسه فقط
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية كمدير على هذه المدرسة.")
            return redirect("reports:select_school")

    # عدد المعلّمين داخل المدرسة النشطة فقط (عزل حسب المدرسة)
    teachers_qs = Teacher.objects.all()
    if active_school is not None:
        teachers_qs = teachers_qs.filter(
            school_memberships__school=active_school,
            school_memberships__is_active=True,
        ).distinct()

    ctx = {
        "reports_count": _filter_by_school(Report.objects.all(), active_school).count(),
        "teachers_count": teachers_qs.count(),
        # ✅ إصلاح: عرض التذاكر الداخلية فقط (is_platform=False)
        "tickets_total": _filter_by_school(Ticket.objects.filter(is_platform=False), active_school).count(),
        "tickets_open": _filter_by_school(Ticket.objects.filter(status__in=["open", "in_progress"], is_platform=False), active_school).count(),
        "tickets_done": _filter_by_school(Ticket.objects.filter(status="done", is_platform=False), active_school).count(),
        "tickets_rejected": _filter_by_school(Ticket.objects.filter(status="rejected", is_platform=False), active_school).count(),
        "has_dept_model": Department is not None,
        "active_school": active_school,
    }

    has_reporttype = False
    reporttypes_count = 0
    try:
        from .models import ReportType  # type: ignore
        has_reporttype = True

        # في لوحة المدير نعرض عدد أنواع التقارير المستخدمة داخل المدرسة الحالية فقط
        if active_school is not None:
            reporttypes_count = (
                Report.objects.filter(school=active_school, category__isnull=False)
                .values("category_id")
                .distinct()
                .count()
            )
        else:
            # في حال عدم وجود مدرسة نشطة نرجع للعدّاد الكلي
            if hasattr(ReportType, "is_active"):
                reporttypes_count = ReportType.objects.filter(is_active=True).count()
            else:
                reporttypes_count = ReportType.objects.count()
    except Exception:
        pass

    ctx.update({
        "has_reporttype": has_reporttype,
        "reporttypes_count": reporttypes_count,
    })

    return render(request, "reports/admin_dashboard.html", ctx)


# ---- لوحة إدارة المنصة (سوبر آدمن) ----
@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_admin_dashboard(request: HttpRequest) -> HttpResponse:
    """لوحة تحكم خاصة بالمشرف العام لإدارة المنصة بالكامل."""

    reports_count = Report.objects.count()
    teachers_count = Teacher.objects.count()
    
    # ✅ إصلاح: عرض تذاكر الدعم الفني فقط (is_platform=True)
    tickets_total = Ticket.objects.filter(is_platform=True).count()
    tickets_open = Ticket.objects.filter(status__in=["open", "in_progress"], is_platform=True).count()
    tickets_done = Ticket.objects.filter(status="done", is_platform=True).count()
    tickets_rejected = Ticket.objects.filter(status="rejected", is_platform=True).count()

    platform_schools_total = School.objects.count()
    platform_schools_active = School.objects.filter(is_active=True).count()
    platform_managers_count = (
        Teacher.objects.filter(
            school_memberships__role_type=SchoolMembership.RoleType.MANAGER,
            school_memberships__is_active=True,
        )
        .distinct()
        .count()
    )

    has_reporttype = False
    reporttypes_count = 0
    try:
        from .models import ReportType  # type: ignore
        has_reporttype = True
        if hasattr(ReportType, "is_active"):
            reporttypes_count = ReportType.objects.filter(is_active=True).count()
        else:
            reporttypes_count = ReportType.objects.count()
    except Exception:
        pass

    ctx = {
        "reports_count": reports_count,
        "teachers_count": teachers_count,
        "tickets_total": tickets_total,
        "tickets_open": tickets_open,
        "tickets_done": tickets_done,
        "tickets_rejected": tickets_rejected,
        "platform_schools_total": platform_schools_total,
        "platform_schools_active": platform_schools_active,
        "platform_managers_count": platform_managers_count,
        "has_reporttype": has_reporttype,
        "reporttypes_count": reporttypes_count,
        
        # إحصائيات الاشتراكات والمالية
        "subscriptions_active": SchoolSubscription.objects.filter(is_active=True, end_date__gte=timezone.now().date()).count(),
        "subscriptions_expired": SchoolSubscription.objects.filter(Q(is_active=False) | Q(end_date__lt=timezone.now().date())).count(),
        "subscriptions_expiring_soon": SchoolSubscription.objects.filter(
            is_active=True,
            end_date__gte=timezone.now().date(),
            end_date__lte=timezone.now().date() + timedelta(days=30)
        ).count(),
        "pending_payments": Payment.objects.filter(status=Payment.Status.PENDING).count(),
        "total_revenue": Payment.objects.filter(status=Payment.Status.APPROVED).aggregate(total=Sum('amount'))['total'] or 0,
    }

    return render(request, "reports/platform_admin_dashboard.html", ctx)


# =========================
# صفحات إدارة المنصة المخصصة (بديلة للآدمن)
# =========================

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_subscriptions_list(request: HttpRequest) -> HttpResponse:
    today = timezone.now().date()
    status = (request.GET.get("status") or "all").strip().lower()
    plan_id = (request.GET.get("plan") or "").strip()

    subscriptions = SchoolSubscription.objects.select_related('school', 'plan').order_by('-start_date')
    if status == "active":
        subscriptions = subscriptions.filter(is_active=True, end_date__gte=today)
    elif status == "expired":
        subscriptions = subscriptions.filter(Q(is_active=False) | Q(end_date__lt=today))

    if plan_id:
        subscriptions = subscriptions.filter(plan_id=plan_id)

    plans = SubscriptionPlan.objects.all().order_by("price", "name")

    return render(
        request,
        "reports/platform_subscriptions.html",
        {"subscriptions": subscriptions, "status": status, "plans": plans, "plan_id": plan_id},
    )

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_plans_list(request: HttpRequest) -> HttpResponse:
    plans = SubscriptionPlan.objects.all().order_by('price')
    return render(request, "reports/platform_plans.html", {"plans": plans})

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_payments_list(request: HttpRequest) -> HttpResponse:
    payments = Payment.objects.select_related('school').order_by('-created_at')
    return render(request, "reports/platform_payments.html", {"payments": payments})

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_payment_detail(request: HttpRequest, pk: int) -> HttpResponse:
    payment = get_object_or_404(Payment, pk=pk)
    
    if request.method == "POST":
        new_status = request.POST.get("status")
        notes = request.POST.get("notes")
        
        if new_status in Payment.Status.values:
            payment.status = new_status
        
        if notes is not None:
            payment.notes = notes
            
        payment.save()
        messages.success(request, "تم تحديث حالة الدفع بنجاح.")
        return redirect("reports:platform_payment_detail", pk=pk)

    return render(request, "reports/platform_payment_detail.html", {"payment": payment})

@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_tickets_list(request: HttpRequest) -> HttpResponse:
    # تذاكر الدعم الفني فقط
    tickets = Ticket.objects.filter(is_platform=True).select_related('creator').order_by('-created_at')
    return render(request, "reports/platform_tickets.html", {"tickets": tickets})


# ---- الأقسام: عرض/إنشاء/تعديل/حذف ----
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def departments_list(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    depts = _all_departments(active_school)
    return render(
        request,
        "reports/departments_list.html",
        {"departments": depts, "has_dept_model": Department is not None},
    )

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_create(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    FormCls = get_department_form()
    if not (Department is not None and FormCls is not None):
        messages.error(request, "إنشاء الأقسام يتطلب تفعيل موديل Department.")
        return redirect("reports:departments_list")

    form = FormCls(request.POST or None, active_school=active_school)
    if request.method == "POST":
        if form.is_valid():
            dep = form.save(commit=False)
            if hasattr(dep, "school") and active_school is not None:
                dep.school = active_school
            dep.save()
            # حفظ علاقات M2M بعد الحفظ الأولي
            if hasattr(form, "save_m2m"):
                form.save_m2m()
            messages.success(request, "✅ تم إنشاء القسم.")
            return redirect("reports:departments_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/department_form.html", {"form": form, "mode": "create"})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_update(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")
    FormCls = get_department_form()
    if not (Department is not None and FormCls is not None):
        messages.error(request, "نموذج الأقسام غير مُعد بعد.")
        return redirect("reports:departments_list")
    dep = get_object_or_404(Department, pk=pk, school=active_school)  # type: ignore[arg-type]
    form = FormCls(request.POST or None, instance=dep, active_school=active_school)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "✏️ تم تحديث بيانات القسم.")
        return redirect("reports:departments_list")
    return render(request, "reports/department_form.html", {"form": form, "title": "تعديل قسم", "dep": dep})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_edit(request: HttpRequest, code: str) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")
    if Department is None:
        messages.error(request, "تعديل الأقسام غير متاح بدون موديل Department.")
        return redirect("reports:departments_list")

    obj, _, label = _resolve_department_by_code_or_pk(str(code), active_school)
    if not obj:
        messages.error(request, "القسم غير موجود.")
        return redirect("reports:departments_list")

    # عزل المدرسة: منع تعديل قسم يخص مدرسة أخرى.
    # الأقسام العامة (school is NULL) يسمح بها للسوبر فقط.
    try:
        if not getattr(request.user, "is_superuser", False) and hasattr(obj, "school_id"):
            if getattr(obj, "school_id", None) is None:
                messages.error(request, "لا يمكنك تعديل قسم عام.")
                return redirect("reports:departments_list")
            if active_school is None or getattr(obj, "school_id", None) != getattr(active_school, "id", None):
                messages.error(request, "لا يمكنك تعديل قسم من مدرسة أخرى.")
                return redirect("reports:departments_list")
    except Exception:
        pass

    FormCls = get_department_form()
    if not FormCls:
        messages.error(request, "DepartmentForm غير متاح.")
        return redirect("reports:departments_list")

    form = FormCls(request.POST or None, instance=obj, active_school=active_school)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, f"✏️ تم تحديث قسم «{label}».")
            return redirect("reports:departments_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/department_form.html", {"form": form, "mode": "edit", "department": obj})

def _assign_role_by_slug(teacher: Teacher, slug: str) -> bool:
    role_obj = Role.objects.filter(slug=slug).first()
    if not role_obj:
        return False
    teacher.role = role_obj
    try:
        teacher.save(update_fields=["role"])
    except Exception:
        teacher.save()
    return True


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_manager_create(request: HttpRequest) -> HttpResponse:
    """إنشاء حساب مدير مدرسة وربطه بمدرسة واحدة على الأقل (ويمكن بأكثر من مدرسة).

    - يستخدم نموذج مبسّط (ManagerCreateForm) لإنشاء مستخدم مدير.
    - بعد الإنشاء يتم إسناد الدور "manager" وضبط عضويات SchoolMembership كمدير.
    """
    # مدارس متاحة للاختيار
    schools = School.objects.filter(is_active=True).order_by("name")
    initial_school_id = request.GET.get("school_id")

    form = ManagerCreateForm(request.POST or None)
    selected_ids = request.POST.getlist("schools") if request.method == "POST" else ([] if not initial_school_id else [initial_school_id])

    if request.method == "POST":
        if not selected_ids:
            messages.error(request, "يجب ربط المدير بمدرسة واحدة على الأقل.")
        if form.is_valid() and selected_ids:
            try:
                with transaction.atomic():
                    teacher = form.save(commit=True)
                    # ضمان أن يكون الدور "manager" إن وُجد
                    _assign_role_by_slug(teacher, MANAGER_SLUG)

                    valid_schools = School.objects.filter(id__in=selected_ids, is_active=True)
                    if not valid_schools:
                        raise ValidationError("لا توجد مدارس صالحة للربط.")

                    # منع أكثر من مدير نشط واحد لكل مدرسة
                    conflict_exists = SchoolMembership.objects.filter(
                        school__in=valid_schools,
                        role_type=SchoolMembership.RoleType.MANAGER,
                        is_active=True,
                    ).exists()
                    if conflict_exists:
                        raise ValidationError("إحدى المدارس المختارة لديها مدير نشط بالفعل. لا يمكن تعيين أكثر من مدير واحد للمدرسة.")

                    for s in valid_schools:
                        SchoolMembership.objects.update_or_create(
                            school=s,
                            teacher=teacher,
                            role_type=SchoolMembership.RoleType.MANAGER,
                            defaults={"is_active": True},
                        )
                messages.success(request, "تم إنشاء مدير المدرسة وربطه بالمدارس المحددة.")
                return redirect("reports:schools_admin_list")
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
            except Exception:
                logger.exception("school_manager_create failed")
                messages.error(request, "تعذّر إنشاء مدير المدرسة. تحقّق من البيانات وحاول مرة أخرى.")

    context = {
        "form": form,
        "schools": schools,
        "selected_ids": [str(i) for i in selected_ids],
        "mode": "create",
        "manager": None,
    }
    return render(request, "reports/school_manager_create.html", context)


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET"])
def school_managers_list(request: HttpRequest) -> HttpResponse:
    """قائمة مدراء المدارس على مستوى المنصة."""
    # نعتبر أي مستخدم مدير منصة إذا:
    # - كان دوره role.slug يطابق MANAGER_SLUG
    #   أو
    # - لديه عضوية SchoolMembership كمدير في أي مدرسة.
    managers_qs = (
        Teacher.objects.filter(is_active=True)
        .filter(
            Q(role__slug__iexact=MANAGER_SLUG)
            | Q(
                school_memberships__role_type=SchoolMembership.RoleType.MANAGER,
                school_memberships__is_active=True,
            )
        )
        .distinct()
        .order_by("name")
        .prefetch_related("school_memberships__school")
    )

    items: list[dict] = []
    for t in managers_qs:
        schools = [
            m.school
            for m in t.school_memberships.all()
            if m.school and m.role_type == SchoolMembership.RoleType.MANAGER and m.is_active
        ]
        items.append({"manager": t, "schools": schools})

    return render(request, "reports/school_managers_list.html", {"managers": items})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["POST"])
def school_manager_delete(request: HttpRequest, pk: int) -> HttpResponse:
    """إيقاف مدير مدرسة وتعطيل عضوياته كمدير.

    لا نحذف السجل نهائيًا للحفاظ على السجلات المرتبطة، وإنما:
      - نضع is_active=False على المستخدم.
      - نعطّل جميع عضويات SchoolMembership الخاصة به كمدير.
    """

    manager = get_object_or_404(Teacher, pk=pk)

    try:
        with transaction.atomic():
            if manager.is_active:
                manager.is_active = False
                manager.save(update_fields=["is_active"])

            SchoolMembership.objects.filter(
                teacher=manager,
                role_type=SchoolMembership.RoleType.MANAGER,
            ).update(is_active=False)

        messages.success(request, "🗑️ تم إيقاف حساب المدير وإلغاء صلاحياته في المدارس.")
    except Exception:
        logger.exception("school_manager_delete failed")
        messages.error(request, "تعذّر حذف المدير. حاول لاحقًا.")

    return redirect("reports:school_managers_list")


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_manager_update(request: HttpRequest, pk: int) -> HttpResponse:
    """تعديل بيانات مدير مدرسة موجود باستخدام نفس نموذج الإنشاء.

    - يمكن ترك كلمة المرور فارغة للإبقاء على الحالية.
    - يمكن تغيير المدارس المرتبطة بالمدير.
    """

    manager = get_object_or_404(
        Teacher.objects.prefetch_related("school_memberships__school"),
        pk=pk,
    )

    schools = School.objects.filter(is_active=True).order_by("name")

    if request.method == "POST":
        form = ManagerCreateForm(request.POST or None, instance=manager)
        selected_ids = request.POST.getlist("schools")
        if not selected_ids:
            messages.error(request, "يجب ربط المدير بمدرسة واحدة على الأقل.")
        if form.is_valid() and selected_ids:
            try:
                with transaction.atomic():
                    teacher = form.save(commit=True)
                    _assign_role_by_slug(teacher, MANAGER_SLUG)

                    valid_schools = School.objects.filter(id__in=selected_ids, is_active=True)

                    # منع أكثر من مدير نشط واحد لكل مدرسة: نسمح فقط إن كانت المدرسة
                    # بدون مدير أو أن المدير الحالي هو نفس المستخدم الجاري تعديله.
                    conflict_exists = SchoolMembership.objects.filter(
                        school__in=valid_schools,
                        role_type=SchoolMembership.RoleType.MANAGER,
                        is_active=True,
                    ).exclude(teacher=teacher).exists()
                    if conflict_exists:
                        raise ValidationError("إحدى المدارس المختارة لديها مدير آخر نشط بالفعل. لا يمكن تعيين أكثر من مدير واحد للمدرسة.")

                    # تعطيل أي عضويات إدارة مدارس لم تعد مختارة
                    SchoolMembership.objects.filter(
                        teacher=teacher,
                        role_type=SchoolMembership.RoleType.MANAGER,
                    ).exclude(school__in=valid_schools).update(is_active=False)

                    # تفعيل/إنشاء العضويات المختارة
                    for s in valid_schools:
                        SchoolMembership.objects.update_or_create(
                            school=s,
                            teacher=teacher,
                            role_type=SchoolMembership.RoleType.MANAGER,
                            defaults={"is_active": True},
                        )
                messages.success(request, "تم تحديث بيانات مدير المدرسة بنجاح.")
                return redirect("reports:school_managers_list")
            except ValidationError as e:
                messages.error(request, " ".join(e.messages))
            except Exception:
                logger.exception("school_manager_update failed")
                messages.error(request, "تعذّر تحديث بيانات مدير المدرسة. تحقّق من البيانات وحاول مرة أخرى.")
        # في حال وجود أخطاء نمرّر selected_ids كما هي
    else:
        existing_ids = SchoolMembership.objects.filter(
            teacher=manager,
            role_type=SchoolMembership.RoleType.MANAGER,
            is_active=True,
        ).values_list("school_id", flat=True)
        selected_ids = [str(i) for i in existing_ids]
        form = ManagerCreateForm(instance=manager)

    context = {
        "form": form,
        "schools": schools,
        "selected_ids": [str(i) for i in selected_ids],
        "mode": "edit",
        "manager": manager,
    }
    return render(request, "reports/school_manager_create.html", context)

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def department_delete(request: HttpRequest, code: str) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    if Department is None:
        messages.error(request, "حذف الأقسام غير متاح بدون موديل Department.")
        return redirect("reports:departments_list")

    obj, _, label = _resolve_department_by_code_or_pk(str(code), active_school)
    if not obj:
        messages.error(request, "القسم غير موجود.")
        return redirect("reports:departments_list")

    # عزل المدرسة: لا يُسمح بحذف قسم يخص مدرسة أخرى.
    # الأقسام العامة (school is NULL) يُسمح بها للسوبر فقط (باستثناء قسم المدير الدائم الذي يمنع حذفه أصلاً).
    try:
        dep_school_id = getattr(obj, "school_id", None)
        if dep_school_id is None:
            if not getattr(request.user, "is_superuser", False):
                messages.error(request, "لا يمكنك حذف قسم عام على مستوى المنصة.")
                return redirect("reports:departments_list")
        elif active_school is not None and dep_school_id != active_school.id:
            messages.error(request, "لا يمكنك حذف قسم يخص مدرسة أخرى.")
            return redirect("reports:departments_list")
    except Exception:
        pass

    try:
        obj.delete()
        messages.success(request, f"🗑️ تم حذف قسم «{label}».")
    except ProtectedError:
        messages.error(request, f"لا يمكن حذف «{label}» لوجود سجلات مرتبطة به. عطّل القسم أو احذف السجلات المرتبطة أولاً.")
    except Exception:
        logger.exception("department_delete failed")
        messages.error(request, "تعذّر حذف القسم.")
    return redirect("reports:departments_list")

def _dept_m2m_field_name_to_teacher(dep_obj) -> str | None:
    try:
        if dep_obj is None:
            return None
        for f in dep_obj._meta.get_fields():
            if isinstance(f, ManyToManyField) and getattr(f.remote_field, "model", None) is Teacher:
                return f.name
    except Exception:
        logger.exception("Failed to detect forward M2M Department→Teacher")
    return None

def _deptmember_field_names() -> tuple[str | None, str | None]:
    dep_field = tea_field = None
    try:
        if DepartmentMembership is None:
            return (None, None)

        for f in DepartmentMembership._meta.get_fields():
            if isinstance(f, ForeignKey):
                if getattr(f.remote_field, "model", None) is Department and dep_field is None:
                    dep_field = f.name
                elif getattr(f.remote_field, "model", None) is Teacher and tea_field is None:
                    tea_field = f.name
            if dep_field and tea_field:
                break

        if dep_field is None:
            for n in ("department", "dept", "dept_fk"):
                if hasattr(DepartmentMembership, n):
                    dep_field = n
                    break
        if tea_field is None:
            for n in ("teacher", "member", "user", "teacher_fk"):
                if hasattr(DepartmentMembership, n):
                    tea_field = n
                    break
    except Exception:
        logger.exception("Failed to detect DepartmentMembership FKs")

    return (dep_field, tea_field)

def _dept_add_member(dep, teacher: Teacher) -> bool:
    try:
        m2m_name = _dept_m2m_field_name_to_teacher(dep)
        if m2m_name:
            getattr(dep, m2m_name).add(teacher)
            return True
    except Exception:
        logger.exception("Add via Department M2M failed")

    try:
        if DepartmentMembership is not None and Department is not None:
            dep_field, tea_field = _deptmember_field_names()
            if dep_field and tea_field:
                kwargs = {dep_field: dep, tea_field: teacher}
                DepartmentMembership.objects.get_or_create(**kwargs)
                return True
    except Exception:
        logger.exception("Add via DepartmentMembership failed")

    return False

def _dept_remove_member(dep, teacher: Teacher) -> bool:
    try:
        m2m_name = _dept_m2m_field_name_to_teacher(dep)
        if m2m_name:
            getattr(dep, m2m_name).remove(teacher)
            return True
    except Exception:
        logger.exception("Remove via Department M2M failed")

    try:
        if DepartmentMembership is not None and Department is not None:
            dep_field, tea_field = _deptmember_field_names()
            if dep_field and tea_field:
                kwargs = {dep_field: dep, tea_field: teacher}
                deleted, _ = DepartmentMembership.objects.filter(**kwargs).delete()
                return deleted > 0
    except Exception:
        logger.exception("Remove via DepartmentMembership failed")

    return False

def _dept_set_member_role(dep, teacher: Teacher, role_type: str) -> bool:
    try:
        if DepartmentMembership is None or Department is None:
            return False

        dep_field, tea_field = _deptmember_field_names()
        if not dep_field or not tea_field:
            return False

        if not hasattr(DepartmentMembership, "role_type"):
            return False

        kwargs = {dep_field: dep, tea_field: teacher}
        obj, created = DepartmentMembership.objects.get_or_create(
            defaults={"role_type": role_type},
            **kwargs,
        )
        if (not created) and getattr(obj, "role_type", None) != role_type:
            obj.role_type = role_type
            obj.save(update_fields=["role_type"])
        return True
    except Exception:
        logger.exception("Failed to set DepartmentMembership role_type")
        return False

def _dept_set_officer(dep, teacher: Teacher) -> bool:
    try:
        if DepartmentMembership is None or Department is None:
            return False

        dep_field, tea_field = _deptmember_field_names()
        if not dep_field or not tea_field:
            return False

        if not hasattr(DepartmentMembership, "role_type"):
            return False

        qs = DepartmentMembership.objects.filter(**{dep_field: dep})
        qs.filter(role_type=DM_OFFICER).exclude(**{tea_field: teacher}).update(role_type=DM_TEACHER)
        return _dept_set_member_role(dep, teacher, DM_OFFICER)
    except Exception:
        logger.exception("Failed to set department officer")
        return False

def _dept_unset_officer(dep, teacher: Teacher) -> bool:
    try:
        if DepartmentMembership is None or Department is None:
            return False

        dep_field, tea_field = _deptmember_field_names()
        if not dep_field or not tea_field:
            return False

        if not hasattr(DepartmentMembership, "role_type"):
            return False

        updated = DepartmentMembership.objects.filter(**{dep_field: dep, tea_field: teacher}).update(role_type=DM_TEACHER)
        return updated > 0
    except Exception:
        logger.exception("Failed to unset department officer")
        return False

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def department_members(request: HttpRequest, code: str | int) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    obj, dept_code, dept_label = _resolve_department_by_code_or_pk(str(code), active_school)
    if not dept_code:
        messages.error(request, "القسم غير موجود.")
        return redirect("reports:departments_list")

    # عزل المدرسة: لا نسمح بإدارة أعضاء قسم تابع لمدرسة أخرى.
    try:
        if obj is not None:
            dep_school_id = getattr(obj, "school_id", None)
            if dep_school_id is None:
                if not getattr(request.user, "is_superuser", False):
                    messages.error(request, "لا يمكنك إدارة قسم عام على مستوى المنصة.")
                    return redirect("reports:departments_list")
            elif active_school is not None and dep_school_id != active_school.id:
                messages.error(request, "لا يمكنك إدارة قسم يخص مدرسة أخرى.")
                return redirect("reports:departments_list")
    except Exception:
        pass

    if request.method == "POST":
        teacher_id = request.POST.get("teacher_id")
        action = (request.POST.get("action") or "").strip()

        allowed_teachers = Teacher.objects.filter(is_active=True)
        if active_school is not None:
            allowed_teachers = allowed_teachers.filter(
                school_memberships__school=active_school,
                school_memberships__is_active=True,
            )
        teacher = allowed_teachers.filter(pk=teacher_id).first()
        if not teacher:
            messages.error(request, "المعلّم غير موجود.")
            return redirect("reports:department_members", code=dept_code)

        if Department is not None and obj:
            try:
                with transaction.atomic():
                    ok = False
                    if action == "add":
                        ok = _dept_set_member_role(obj, teacher, DM_TEACHER) or _dept_add_member(obj, teacher)
                        if ok:
                            messages.success(request, f"تم تكليف {teacher.name} في قسم «{dept_label}».")
                        else:
                            messages.error(request, "تعذّر إسناد المعلّم — تحقّق من بنية DepartmentMembership.")
                    elif action == "set_officer":
                        ok = _dept_set_officer(obj, teacher)
                        if ok:
                            messages.success(request, f"تم تعيين {teacher.name} مسؤولاً لقسم «{dept_label}». ")
                        else:
                            messages.error(request, "تعذّر تعيين مسؤول القسم — تحقّق من دعم role_type.")
                    elif action == "unset_officer":
                        ok = _dept_remove_member(obj, teacher)
                        if ok:
                            messages.success(request, f"تم إلغاء تكليف {teacher.name} من القسم.")
                        else:
                            messages.error(request, "تعذّر إلغاء التكليف.")
                    elif action == "remove":
                        ok = _dept_remove_member(obj, teacher)
                        if ok:
                            messages.success(request, f"تم إلغاء تكليف {teacher.name}.")
                        else:
                            messages.error(request, "تعذّر إلغاء التكليف — تحقق من بنية العلاقات.")
                    else:
                        messages.error(request, "إجراء غير معروف.")
            except Exception:
                logger.exception("department_members mutation failed")
                messages.error(request, "حدث خطأ أثناء حفظ التغييرات.")
        else:
            messages.error(request, "إدارة الأعضاء تتطلب تفعيل موديل Department.")
            return redirect("reports:departments_list")

        return redirect("reports:department_members", code=dept_code)

    members_qs = _members_for_department(dept_code, active_school)

    officers_qs = Teacher.objects.none()
    teachers_qs = Teacher.objects.none()
    assigned_ids_qs = Teacher.objects.none()
    try:
        if DepartmentMembership is not None:
            mem_qs = DepartmentMembership.objects.filter(department__slug__iexact=dept_code)
            if active_school is not None:
                mem_qs = mem_qs.filter(department__school=active_school)
            officer_ids = mem_qs.filter(role_type=DM_OFFICER).values_list("teacher_id", flat=True)
            teacher_ids = mem_qs.filter(role_type=DM_TEACHER).values_list("teacher_id", flat=True)
            assigned_ids = mem_qs.values_list("teacher_id", flat=True)

            officers_qs = Teacher.objects.filter(is_active=True, id__in=officer_ids).distinct().order_by("name")
            teachers_qs = Teacher.objects.filter(is_active=True, id__in=teacher_ids).distinct().order_by("name")
            assigned_ids_qs = Teacher.objects.filter(id__in=assigned_ids)

            if active_school is not None:
                officers_qs = officers_qs.filter(
                    school_memberships__school=active_school,
                    school_memberships__is_active=True,
                )
                teachers_qs = teachers_qs.filter(
                    school_memberships__school=active_school,
                    school_memberships__is_active=True,
                )
                assigned_ids_qs = assigned_ids_qs.filter(
                    school_memberships__school=active_school,
                    school_memberships__is_active=True,
                )
    except Exception:
        logger.exception("Failed to compute officers/teachers memberships")

    all_teachers = Teacher.objects.filter(is_active=True)
    if active_school is not None:
        all_teachers = all_teachers.filter(
            school_memberships__school=active_school,
            school_memberships__is_active=True,
        )
    all_teachers = all_teachers.order_by("name")

    try:
        if hasattr(assigned_ids_qs, "values_list"):
            assigned_ids_list = assigned_ids_qs.values_list("id", flat=True)
            available = all_teachers.exclude(id__in=assigned_ids_list)
        else:
            available = all_teachers
    except Exception:
        available = all_teachers

    return render(
        request,
        "reports/department_members.html",
        {
            "department": obj if obj else {"code": dept_code, "name": dept_label},
            "dept_code": dept_code,
            "dept_label": dept_label,
            "members": members_qs,
            "officers": officers_qs,
            "teachers": teachers_qs,
            "all_teachers": all_teachers,
            "available_teachers": available,
            "has_dept_model": Department is not None,
        },
    )

# ===== ReportType CRUD =====
@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def reporttypes_list(request: HttpRequest) -> HttpResponse:
    if not (ReportType is not None):
        messages.error(request, "إدارة الأنواع تتطلب تفعيل موديل ReportType وتشغيل الهجرات.")
        return render(request, "reports/reporttypes_list.html", {"items": [], "db_backed": False})

    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    qs = ReportType.objects.all().order_by("order", "name")
    if active_school is not None and hasattr(ReportType, "school"):
        qs = qs.filter(school=active_school)
    items = []
    for rt in qs:
        cnt_qs = Report.objects.filter(category__code=rt.code)
        try:
            if hasattr(Report, "school"):
                rt_school_id = getattr(rt, "school_id", None)
                if rt_school_id is not None:
                    cnt_qs = cnt_qs.filter(school_id=rt_school_id)
                elif active_school is not None:
                    cnt_qs = cnt_qs.filter(school=active_school)
        except Exception:
            pass
        cnt = cnt_qs.count()
        items.append({"obj": rt, "code": rt.code, "name": rt.name, "is_active": rt.is_active, "order": rt.order, "count": cnt})
    return render(request, "reports/reporttypes_list.html", {"items": items, "db_backed": True})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def reporttype_create(request: HttpRequest) -> HttpResponse:
    if ReportType is None:
        messages.error(request, "إنشاء الأنواع يتطلب تفعيل موديل ReportType.")
        return redirect("reports:reporttypes_list")

    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    try:
        from .forms import ReportTypeForm  # type: ignore
        FormCls = ReportTypeForm
    except Exception:
        class _RTForm(forms.ModelForm):
            class Meta:
                model = ReportType
                fields = ("name", "code", "description", "order", "is_active")
        FormCls = _RTForm

    form = FormCls(request.POST or None)
    if request.method == "POST":
        if form.is_valid():
            obj = form.save(commit=False)
            if hasattr(obj, "school") and active_school is not None:
                obj.school = active_school
            obj.save()
            if hasattr(form, "save_m2m"):
                form.save_m2m()
            messages.success(request, "✅ تم إضافة نوع التقرير.")
            return redirect("reports:reporttypes_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/reporttype_form.html", {"form": form, "mode": "create"})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def reporttype_update(request: HttpRequest, pk: int) -> HttpResponse:
    if ReportType is None:
        messages.error(request, "تعديل الأنواع يتطلب تفعيل موديل ReportType.")
        return redirect("reports:reporttypes_list")

    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    obj = get_object_or_404(ReportType, pk=pk, school=active_school)

    try:
        from .forms import ReportTypeForm  # type: ignore
        FormCls = ReportTypeForm
    except Exception:
        class _RTForm(forms.ModelForm):
            class Meta:
                model = ReportType
                fields = ("name", "code", "description", "order", "is_active")
        FormCls = _RTForm

    form = FormCls(request.POST or None, instance=obj)
    if request.method == "POST":
        if form.is_valid():
            form.save()
            messages.success(request, "✏️ تم تعديل نوع التقرير.")
            return redirect("reports:reporttypes_list")
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/reporttype_form.html", {"form": form, "mode": "edit", "obj": obj})

@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def reporttype_delete(request: HttpRequest, pk: int) -> HttpResponse:
    if ReportType is None:
        messages.error(request, "حذف الأنواع يتطلب تفعيل موديل ReportType.")
        return redirect("reports:reporttypes_list")

    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return redirect("reports:select_school")

    # عزل المدرسة: لا نسمح بحذف نوع تقرير يخص مدرسة أخرى.
    # الأنواع العامة (school is NULL) يسمح بها للسوبر فقط.
    if getattr(request.user, "is_superuser", False):
        obj = get_object_or_404(ReportType, pk=pk)
    else:
        obj = get_object_or_404(ReportType, pk=pk, school=active_school)

    # حساب الاستخدام وفق نطاق المدرسة.
    try:
        if getattr(obj, "school_id", None) is not None:
            used = Report.objects.filter(category__code=obj.code, school_id=obj.school_id).count()
        else:
            used = Report.objects.filter(category__code=obj.code).count()
    except Exception:
        used = Report.objects.filter(category__code=obj.code).count()
    if used > 0:
        messages.error(request, f"لا يمكن حذف «{obj.name}» لوجود {used} تقرير مرتبط. يمكنك تعطيله بدلًا من الحذف.")
        return redirect("reports:reporttypes_list")

    try:
        obj.delete()
        messages.success(request, f"🗑️ تم حذف «{obj.name}».")
    except Exception:
        logger.exception("reporttype_delete failed")
        messages.error(request, "تعذّر حذف نوع التقرير.")

    return redirect("reports:reporttypes_list")

# =========================
# واجهة برمجية مساعدة
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def api_department_members(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    dept = (request.GET.get("department") or "").strip()
    if not dept:
        return JsonResponse({"results": []})

    users = _members_for_department(dept, active_school).values("id", "name")
    return JsonResponse({"results": list(users)})

# =========================
# صناديق التذاكر بحسب القسم/المُعيّن
# =========================
@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
@require_http_methods(["GET"])
def tickets_inbox(request: HttpRequest) -> HttpResponse:
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists() and active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")
    qs = Ticket.objects.select_related("creator", "assignee", "department").order_by("-created_at")
    qs = _filter_by_school(qs, active_school)
    
    # استبعاد تذاكر الدعم الفني للمنصة (لأنها خاصة بالإدارة العليا)
    qs = qs.filter(is_platform=False)

    is_manager = bool(getattr(getattr(request.user, "role", None), "slug", None) == "manager")
    if not is_manager:
        user_codes = _user_department_codes(request.user)
        qs = qs.filter(Q(assignee=request.user) | Q(department__slug__in=user_codes))

    status = (request.GET.get("status") or "").strip()
    q = (request.GET.get("q") or "").strip()
    mine = request.GET.get("mine") == "1"

    if status:
        qs = qs.filter(status=status)
    if mine:
        qs = qs.filter(assignee=request.user)
    if q:
        for kw in q.split():
            qs = qs.filter(Q(title__icontains=kw) | Q(body__icontains=kw))

    ctx = {
        "tickets": qs[:200],
        "status": status,
        "q": q,
        "mine": mine,
        "status_choices": Ticket.Status.choices,
    }
    return render(request, "reports/tickets_inbox.html", ctx)

@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def assigned_to_me(request: HttpRequest) -> HttpResponse:
    user = request.user
    user_codes = _user_department_codes(user)
    active_school = _get_active_school(request)

    if School.objects.filter(is_active=True).exists() and active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    qs = Ticket.objects.select_related("creator", "assignee", "department").filter(
        Q(assignee=user) | Q(assignee__isnull=True, department__slug__in=user_codes)
    )
    qs = _filter_by_school(qs, active_school)
    
    # استبعاد تذاكر الدعم الفني للمنصة
    qs = qs.filter(is_platform=False)

    q = (request.GET.get("q") or "").strip()
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(creator__name__icontains=q) | Q(id__icontains=q))

    status = request.GET.get("status")
    if status in {"open", "in_progress", "done", "rejected"}:
        qs = qs.filter(status=status)

    order = request.GET.get("order") or "-created_at"
    allowed_order = {"-created_at", "created_at", "-id", "id"}
    if order not in allowed_order:
        order = "-created_at"
    if order in {"created_at", "-created_at"}:
        qs = qs.order_by(order, "-id")
    else:
        qs = qs.order_by(order)

    raw_counts = dict(qs.values("status").annotate(c=Count("id")).values_list("status", "c"))
    stats = {
        "open": raw_counts.get("open", 0),
        "in_progress": raw_counts.get("in_progress", 0),
        "done": raw_counts.get("done", 0),
        "rejected": raw_counts.get("rejected", 0),
    }

    page_obj = Paginator(qs, 12).get_page(request.GET.get("page") or 1)
    view_mode = request.GET.get("view", "list")

    return render(request, "reports/assigned_to_me.html", {"page_obj": page_obj, "stats": stats, "view_mode": view_mode})

# =========================
# تقارير: تعديل/حذف للمستخدم الحالي
# =========================
@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def edit_my_report(request: HttpRequest, pk: int) -> HttpResponse:
    active_school = _get_active_school(request)
    qs = Report.objects.filter(teacher=request.user)
    qs = _filter_by_school(qs, active_school)
    r = get_object_or_404(qs, pk=pk)

    if request.method == "POST":
        form = ReportForm(request.POST, request.FILES, instance=r, active_school=active_school)
        if form.is_valid():
            form.save()
            messages.success(request, "✏️ تم تحديث التقرير بنجاح.")
            nxt = request.POST.get("next") or request.GET.get("next")
            return redirect(nxt or "reports:my_reports")
        messages.error(request, "تحقّق من الحقول.")
    else:
        form = ReportForm(instance=r, active_school=active_school)

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

# =========================
# الإشعارات (إرسال/استقبال)
# =========================
@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["GET", "POST"])
def notifications_create(request: HttpRequest) -> HttpResponse:
    if NotificationCreateForm is None:
        messages.error(request, "نموذج إنشاء الإشعار غير متوفر.")
        return redirect("reports:home")

    # نربط الإشعارات بمدرسة معيّنة للمدير/الضابط عبر المدرسة النشطة
    active_school = None
    try:
        active_school = _get_active_school(request)
    except Exception:
        active_school = None

    # حماية: غير السوبر يجب أن يختار مدرسة نشطة قبل الإرسال
    if not getattr(request.user, "is_superuser", False) and active_school is None:
        messages.error(request, "يرجى اختيار المدرسة أولاً قبل إرسال الإشعارات.")
        return redirect("reports:home")

    form = NotificationCreateForm(request.POST or None, user=request.user, active_school=active_school)
    if request.method == "POST":
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save(creator=request.user, default_school=active_school)
                messages.success(request, "✅ تم إرسال الإشعار.")
                return redirect("reports:notifications_sent")
            except Exception:
                logger.exception("notifications_create failed")
                messages.error(request, "تعذّر الإرسال. جرّب لاحقًا.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء.")
    return render(request, "reports/notifications_create.html", {"form": form, "title": "إنشاء إشعار"})

@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["POST"])
def notification_delete(request: HttpRequest, pk: int) -> HttpResponse:
    if Notification is None:
        messages.error(request, "نموذج الإشعار غير متاح.")
        return redirect("reports:notifications_sent")

    active_school = _get_active_school(request)
    if not request.user.is_superuser and active_school is None:
        messages.error(request, "يرجى اختيار المدرسة أولاً.")
        return redirect("reports:home")

    n = get_object_or_404(Notification, pk=pk)
    role_slug = getattr(getattr(request.user, "role", None), "slug", None)
    is_owner = getattr(n, "created_by_id", None) == request.user.id
    is_manager = bool(request.user.is_superuser or role_slug == "manager")
    if not (is_manager or is_owner):
        messages.error(request, "لا تملك صلاحية حذف هذا الإشعار.")
        return redirect("reports:notifications_sent")

    # عزل حسب المدرسة النشطة (غير السوبر)
    try:
        if not request.user.is_superuser and hasattr(n, "school_id"):
            if getattr(n, "school_id", None) is None:
                messages.error(request, "لا تملك صلاحية حذف إشعار عام.")
                return redirect("reports:notifications_sent")
            if getattr(n, "school_id", None) != getattr(active_school, "id", None):
                messages.error(request, "لا تملك صلاحية حذف إشعار من مدرسة أخرى.")
                return redirect("reports:notifications_sent")
    except Exception:
        pass
    try:
        n.delete()
        messages.success(request, "🗑️ تم حذف الإشعار.")
    except Exception:
        logger.exception("notification_delete failed")
        messages.error(request, "تعذّر حذف الإشعار.")
    return redirect("reports:notifications_sent")

def _recipient_is_read(rec) -> tuple[bool, str | None]:
    for flag in ("is_read", "read", "seen", "opened"):
        if hasattr(rec, flag):
            try:
                return (bool(getattr(rec, flag)), None)
            except Exception:
                pass
    for dt in ("read_at", "seen_at", "opened_at"):
        if hasattr(rec, dt):
            try:
                val = getattr(rec, dt)
                return (bool(val), getattr(val, "strftime", lambda fmt: None)("%Y-%m-%d %H:%M") if val else None)
            except Exception:
                pass
    if hasattr(rec, "status"):
        try:
            st = str(getattr(rec, "status") or "").lower()
            if st in {"read", "seen", "opened", "done"}:
                return (True, None)
        except Exception:
            pass
    return (False, None)

def _arabic_role_label(role_slug: str) -> str:
    return _role_display_map().get((role_slug or "").lower(), role_slug or "")

@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["GET"])
def notification_detail(request: HttpRequest, pk: int) -> HttpResponse:
    if Notification is None:
        messages.error(request, "نموذج الإشعار غير متاح.")
        return redirect("reports:notifications_sent")

    active_school = _get_active_school(request)
    if not request.user.is_superuser and active_school is None:
        messages.error(request, "يرجى اختيار المدرسة أولاً.")
        return redirect("reports:home")

    n = get_object_or_404(Notification, pk=pk)

    # عزل حسب المدرسة النشطة (غير السوبر)
    try:
        if not request.user.is_superuser and hasattr(n, "school_id"):
            if getattr(n, "school_id", None) is None:
                messages.error(request, "لا تملك صلاحية عرض إشعار عام.")
                return redirect("reports:notifications_sent")
            if getattr(n, "school_id", None) != getattr(active_school, "id", None):
                messages.error(request, "لا تملك صلاحية عرض إشعار من مدرسة أخرى.")
                return redirect("reports:notifications_sent")
    except Exception:
        pass

    role_slug = getattr(getattr(request.user, "role", None), "slug", None)
    if not (request.user.is_superuser or role_slug == "manager"):
        if getattr(n, "created_by_id", None) != request.user.id:
            messages.error(request, "لا تملك صلاحية عرض هذا الإشعار.")
            return redirect("reports:notifications_sent")

    body = (
        getattr(n, "message", None) or getattr(n, "body", None) or
        getattr(n, "content", None) or getattr(n, "text", None) or
        getattr(n, "details", None) or ""
    )

    recipients = []
    if NotificationRecipient is not None:
        # اكتشف اسم FK للإشعار
        notif_fk = None
        for f in NotificationRecipient._meta.get_fields():
            if getattr(getattr(f, "remote_field", None), "model", None) is Notification:
                notif_fk = f.name
                break

        # اسم حقل الشخص
        user_fk = None
        for cand in ("teacher", "user", "recipient"):
            if hasattr(NotificationRecipient, cand):
                user_fk = cand
                break

        if notif_fk:
            qs = NotificationRecipient.objects.filter(**{f"{notif_fk}": n})
            if user_fk:
                qs = qs.select_related(f"{user_fk}", f"{user_fk}__role")
            qs = qs.order_by("id")

            for r in qs:
                t = getattr(r, user_fk) if user_fk else None
                if not t:
                    continue
                name = getattr(t, "name", None) or getattr(t, "phone", None) or getattr(t, "username", None) or f"مستخدم #{getattr(t, 'pk', '')}"
                rslug = getattr(getattr(t, "role", None), "slug", "") or ""
                role_label = _arabic_role_label(rslug)
                is_read, read_at_str = _recipient_is_read(r)
                recipients.append({
                    "name": str(name),
                    "role": role_label,
                    "read": bool(is_read),
                    "read_at": read_at_str,
                })

    ctx = {
        "n": n,
        "body": body,
        "recipients": recipients,
    }
    return render(request, "reports/notification_detail.html", ctx)

@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_notifications(request: HttpRequest) -> HttpResponse:
    if NotificationRecipient is None:
        return render(request, "reports/my_notifications.html", {"page_obj": Paginator([], 12).get_page(1)})

    active_school = _get_active_school(request)

    qs = (NotificationRecipient.objects
          .select_related("notification")
          .filter(teacher=request.user)
          .order_by("-created_at", "-id"))

    # عزل حسب المدرسة النشطة (مع السماح بإشعارات عامة school=NULL)
    try:
        if active_school is not None and Notification is not None and hasattr(Notification, "school"):
            qs = qs.filter(Q(notification__school=active_school) | Q(notification__school__isnull=True))
    except Exception:
        pass

    # إخفاء المنتهية بحسب الحقول المتاحة
    now = timezone.now()
    try:
        if Notification is not None and hasattr(Notification, "expires_at"):
            qs = qs.exclude(notification__expires_at__lt=now)
        elif Notification is not None and hasattr(Notification, "ends_at"):
            qs = qs.exclude(notification__ends_at__lt=now)
    except Exception:
        pass

    page = Paginator(qs, 12).get_page(request.GET.get("page") or 1)

    # عند فتح تبويب "إشعاراتي" غالباً يتوقع المستخدم أن تصبح الإشعارات المعروضة كمقروءة.
    # لا يمكن الاعتماد على "إغلاق التبويب" كإشارة مؤكدة من المتصفح، لذا نُحدّثها هنا.
    try:
        items = list(page.object_list)
        unread_ids = [x.pk for x in items if hasattr(x, "is_read") and not bool(getattr(x, "is_read", False))]
        if unread_ids:
            now = timezone.now()
            fields = {f.name for f in NotificationRecipient._meta.get_fields()}
            upd: dict = {}
            if "is_read" in fields:
                upd["is_read"] = True
            if "read_at" in fields:
                upd["read_at"] = now
            if upd:
                NotificationRecipient.objects.filter(pk__in=unread_ids, teacher=request.user).update(**upd)
                for x in items:
                    if x.pk in unread_ids:
                        if "is_read" in upd:
                            setattr(x, "is_read", True)
                        if "read_at" in upd:
                            setattr(x, "read_at", now)
            page.object_list = items
    except Exception:
        pass
    return render(request, "reports/my_notifications.html", {"page_obj": page})

@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["GET"])
def notifications_sent(request: HttpRequest) -> HttpResponse:
    if Notification is None:
        return render(request, "reports/notifications_sent.html", {"page_obj": Paginator([], 20).get_page(1), "stats": {}})

    active_school = _get_active_school(request)
    if not request.user.is_superuser and active_school is None:
        messages.error(request, "يرجى اختيار المدرسة أولاً.")
        return redirect("reports:home")

    qs = Notification.objects.all().order_by("-created_at", "-id")

    # غير السوبر: لا يرى إلا إشعارات المدرسة النشطة (لا إشعارات عامة)
    try:
        if not request.user.is_superuser and hasattr(Notification, "school"):
            qs = qs.filter(school=active_school)
    except Exception:
        pass

    role_slug = getattr(getattr(request.user, "role", None), "slug", None)
    if role_slug and role_slug != "manager" and not request.user.is_superuser:
        qs = qs.filter(created_by=request.user)

    qs = qs.select_related("created_by")
    page = Paginator(qs, 20).get_page(request.GET.get("page") or 1)

    notif_ids = [n.id for n in page.object_list]
    stats: dict[int, dict] = {}

    # حساب read/total بمرونة على NotificationRecipient
    if NotificationRecipient is not None and notif_ids:
        notif_fk_name = None
        try:
            for f in NotificationRecipient._meta.get_fields():
                if getattr(getattr(f, "remote_field", None), "model", None) is Notification:
                    notif_fk_name = f.name
                    break
        except Exception:
            notif_fk_name = None

        if notif_fk_name:
            fields = {f.name for f in NotificationRecipient._meta.get_fields()}
            if "is_read" in fields:
                read_filter = Q(is_read=True)
            elif "read_at" in fields:
                read_filter = Q(read_at__isnull=False)
            elif "seen_at" in fields:
                read_filter = Q(seen_at__isnull=False)
            elif "status" in fields:
                read_filter = Q(status__in=["read", "seen", "opened", "done"])
            else:
                read_filter = Q(pk__in=[])

            rc = (NotificationRecipient.objects
                  .filter(**{f"{notif_fk_name}_id__in": notif_ids})
                  .values(f"{notif_fk_name}_id")
                  .annotate(total=Count("id"), read=Count("id", filter=read_filter)))
            for row in rc:
                stats[row[f"{notif_fk_name}_id"]] = {"total": row["total"], "read": row["read"]}

    # أسماء مستلمين مختصرة
    rec_names_map: dict[int, list[str]] = {i: [] for i in notif_ids}

    def _name_of(person) -> str:
        return (getattr(person, "name", None) or
                getattr(person, "phone", None) or
                getattr(person, "username", None) or
                getattr(person, "national_id", None) or
                str(person))

    for n in page.object_list:
        names_set = set()
        try:
            rel = getattr(n, "recipients", None)
            if rel is not None:
                for t in rel.all()[:12]:
                    if t:
                        nm = _name_of(t)
                        if nm not in names_set:
                            names_set.add(nm)
        except Exception:
            pass
        rec_names_map[n.id] = list(names_set)

    remaining_ids = [nid for nid, arr in rec_names_map.items() if len(arr) < 5]
    if remaining_ids and NotificationRecipient is not None:
        notif_fk_name = None
        try:
            for f in NotificationRecipient._meta.get_fields():
                if getattr(getattr(f, "remote_field", None), "model", None) is Notification:
                    notif_fk_name = f.name
                    break
        except Exception:
            pass

        if notif_fk_name:
            thr_qs = NotificationRecipient.objects.filter(**{f"{notif_fk_name}_id__in": remaining_ids})
            for r in thr_qs:
                nid = getattr(r, f"{notif_fk_name}_id", None)
                if not nid:
                    continue
                person = (getattr(r, "teacher", None) or
                          getattr(r, "user", None) or
                          getattr(r, "recipient", None))
                if person:
                    nm = _name_of(person)
                    arr = rec_names_map.get(nid, [])
                    if nm and nm not in arr and len(arr) < 12:
                        arr.append(nm)
                        rec_names_map[nid] = arr

    for n in page.object_list:
        n.rec_names = rec_names_map.get(n.id, [])

    return render(request, "reports/notifications_sent.html", {"page_obj": page, "stats": stats})

# تعليم الإشعار كمقروء (حسب Recipient pk)
@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def notification_mark_read(request: HttpRequest, pk: int) -> HttpResponse:
    if NotificationRecipient is None:
        return redirect(request.POST.get("next") or "reports:my_notifications")
    item = get_object_or_404(NotificationRecipient, pk=pk, teacher=request.user)
    if not getattr(item, "is_read", False):
        if hasattr(item, "is_read"):
            item.is_read = True
        if hasattr(item, "read_at"):
            item.read_at = timezone.now()
        try:
            if hasattr(item, "is_read") and hasattr(item, "read_at"):
                item.save(update_fields=["is_read", "read_at"])
            else:
                item.save()
        except Exception:
            item.save()
    return redirect(request.POST.get("next") or "reports:my_notifications")

# تحديد الكل كمقروء
@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def notifications_mark_all_read(request: HttpRequest) -> HttpResponse:
    if NotificationRecipient is None:
        return redirect(request.POST.get("next") or "reports:my_notifications")
    qs = NotificationRecipient.objects.filter(teacher=request.user)
    try:
        if "is_read" in {f.name for f in NotificationRecipient._meta.get_fields()}:
            qs = qs.filter(is_read=False)
            qs.update(is_read=True, read_at=timezone.now() if hasattr(NotificationRecipient, "read_at") else None)
        elif "read_at" in {f.name for f in NotificationRecipient._meta.get_fields()}:
            qs = qs.filter(read_at__isnull=True)
            qs.update(read_at=timezone.now())
        else:
            pass
    except Exception:
        for x in qs:
            try:
                if hasattr(x, "is_read"):
                    x.is_read = True
                if hasattr(x, "read_at"):
                    x.read_at = timezone.now()
                x.save()
            except Exception:
                continue
    messages.success(request, "تم تحديد جميع الإشعارات كمقروءة.")
    return redirect(request.POST.get("next") or "reports:my_notifications")

# تعليم الإشعار كمقروء (حسب رقم الإشعار نفسه لا الـRecipient)
@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def notification_mark_read_by_notification(request: HttpRequest, pk: int) -> HttpResponse:
    if NotificationRecipient is None:
        return JsonResponse({"ok": False}, status=400)
    try:
        item = NotificationRecipient.objects.filter(
            notification_id=pk, teacher=request.user
        ).first()
        if item:
            if hasattr(item, "is_read") and not item.is_read:
                item.is_read = True
            if hasattr(item, "read_at") and getattr(item, "read_at", None) is None:
                item.read_at = timezone.now()
            try:
                if hasattr(item, "is_read") and hasattr(item, "read_at"):
                    item.save(update_fields=["is_read", "read_at"])
                else:
                    item.save()
            except Exception:
                item.save()
        return JsonResponse({"ok": True})
    except Exception:
        return JsonResponse({"ok": False}, status=400)

# إبقاء المسار القديم للتوافق الخلفي: تحويل إلى صفحة الإنشاء
@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
def send_notification(request: HttpRequest) -> HttpResponse:
    return redirect("reports:notifications_create")


# =========================
# إدارة الاشتراكات والمالية
# =========================

def subscription_expired(request):
    """صفحة تظهر عند انتهاء الاشتراك.

    نُمرّر معلومات المدرسة + تاريخ انتهاء الاشتراك إن توفّرت لعرضها في الرسالة.
    """
    school = None
    subscription = None
    is_manager = False

    try:
        user = getattr(request, "user", None)
        if user is not None and getattr(user, "is_authenticated", False):
            active_school = _get_active_school(request)
            memberships = (
                SchoolMembership.objects.filter(teacher=user, is_active=True)
                .select_related("school__subscription__plan", "school")
            )
            membership = None
            if active_school:
                membership = memberships.filter(school=active_school).first()
            if membership is None:
                membership = memberships.first()

            if membership is not None:
                school = membership.school
                is_manager = membership.role_type == SchoolMembership.RoleType.MANAGER
                subscription = getattr(school, "subscription", None)

                # لو أصبحت المدرسة النشطة اشتراكها ساري (بعد التبديل مثلاً)،
                # لا معنى لإظهار صفحة الانتهاء.
                try:
                    if subscription is not None and not bool(subscription.is_expired):
                        if getattr(request.user, "is_superuser", False):
                            return redirect("reports:platform_admin_dashboard")
                        if _is_staff(request.user):
                            return redirect("reports:admin_dashboard")
                        return redirect("reports:home")
                except Exception:
                    pass
    except Exception:
        # لا نكسر الصفحة لو كانت هناك مشكلة في العضويات
        school = None
        subscription = None
        is_manager = False

    return render(
        request,
        "reports/subscription_expired.html",
        {"school": school, "subscription": subscription, "is_manager": is_manager},
    )

@login_required(login_url="reports:login")
def my_subscription(request):
    """صفحة عرض تفاصيل الاشتراك لمدير المدرسة"""
    active_school = _get_active_school(request)
    
    # جلب جميع عضويات الإدارة للمستخدم
    memberships = SchoolMembership.objects.filter(
        teacher=request.user, 
        role_type=SchoolMembership.RoleType.MANAGER,
        is_active=True
    ).select_related('school__subscription__plan')
    
    membership = None
    # محاولة استخدام المدرسة النشطة إذا كان المستخدم مديراً فيها
    if active_school:
        membership = memberships.filter(school=active_school).first()
    
    # إذا لم توجد مدرسة نشطة أو المستخدم ليس مديراً فيها، نأخذ أول مدرسة يديرها
    if not membership:
        membership = memberships.first()
    
    if not membership:
        messages.error(request, "عفواً، هذه الصفحة مخصصة لمدير المدرسة فقط.")
        return redirect('reports:home')
        
    subscription = getattr(membership.school, 'subscription', None)
    
    # جلب آخر المدفوعات
    payments = Payment.objects.filter(school=membership.school).order_by('-created_at')[:5]
    
    context = {
        'subscription': subscription,
        'school': membership.school,
        'plans': SubscriptionPlan.objects.filter(is_active=True),
        'payments': payments
    }
    return render(request, 'reports/my_subscription.html', context)

@login_required(login_url="reports:login")
def payment_create(request):
    """صفحة رفع إيصال الدفع"""
    active_school = _get_active_school(request)
    
    memberships = SchoolMembership.objects.filter(
        teacher=request.user, 
        role_type=SchoolMembership.RoleType.MANAGER,
        is_active=True
    )
    
    membership = None
    if active_school:
        membership = memberships.filter(school=active_school).first()
        
    if not membership:
        membership = memberships.first()
    
    if not membership:
        messages.error(request, "عفواً، هذه الصفحة مخصصة لمدير المدرسة فقط.")
        return redirect('reports:home')

    if request.method == 'POST':
        amount = request.POST.get('amount')
        receipt = request.FILES.get('receipt_image')
        notes = request.POST.get('notes')
        
        if not amount or not receipt:
            messages.error(request, "يرجى إدخال المبلغ وإرفاق صورة الإيصال.")
        else:
            Payment.objects.create(
                school=membership.school,
                subscription=getattr(membership.school, 'subscription', None),
                amount=amount,
                receipt_image=receipt,
                notes=notes,
                created_by=request.user
            )
            messages.success(request, "تم رفع طلب الدفع بنجاح، سيتم مراجعته قريباً.")
            return redirect('reports:my_subscription')
            
    return redirect('reports:my_subscription')


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_plan_form(request: HttpRequest, pk: Optional[int] = None) -> HttpResponse:
    """إضافة أو تعديل خطة اشتراك"""
    plan = None
    if pk:
        plan = get_object_or_404(SubscriptionPlan, pk=pk)
    
    if request.method == "POST":
        form = SubscriptionPlanForm(request.POST, instance=plan)
        if form.is_valid():
            form.save()
            messages.success(request, "تم حفظ الخطة بنجاح.")
            return redirect("reports:platform_plans_list")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = SubscriptionPlanForm(instance=plan)
    
    return render(request, "reports/platform_plan_form.html", {"form": form, "plan": plan})


@login_required(login_url="reports:login")
@user_passes_test(lambda u: getattr(u, "is_superuser", False), login_url="reports:login")
def platform_subscription_form(request: HttpRequest, pk: Optional[int] = None) -> HttpResponse:
    """إضافة أو تعديل اشتراك مدرسة"""
    subscription = None
    if pk:
        subscription = get_object_or_404(SchoolSubscription, pk=pk)
    
    if request.method == "POST":
        form = SchoolSubscriptionForm(request.POST, instance=subscription)
        if form.is_valid():
            form.save()
            messages.success(request, "تم حفظ الاشتراك بنجاح.")
            return redirect("reports:platform_subscriptions_list")
        else:
            messages.error(request, "يرجى تصحيح الأخطاء أدناه.")
    else:
        form = SchoolSubscriptionForm(instance=subscription)
    
    return render(request, "reports/platform_subscription_form.html", {"form": form, "subscription": subscription})
