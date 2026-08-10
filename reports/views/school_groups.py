# -*- coding: utf-8 -*-
"""لوحة المدير التنفيذي لمجموعة المدارس المتكاملة.

هذه أول شاشة في المشروع لا تنتمي إلى مدرسة واحدة: فهي تقرأ عبر مدارس المجموعة
مجتمعةً، ولذلك لا تمرّ بـ``_get_active_school`` ولا تلمس ``active_school_id``
في الجلسة. إبقاء سياق المدرسة سليماً هنا مقصود — فالمدير التنفيذي قد يتنقل
بين اللوحة ومدرسة بعينها، ولا يصح أن تعيد اللوحة ضبط سياقه.

كل ما في هذا الملف قراءة فقط. المدير التنفيذي يشرف ويتابع، ولا يتولى الإدارة
اليومية لأي مدرسة، ولا ينتقص من صلاحيات مديرها.
"""
from __future__ import annotations

from datetime import timedelta

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Q
from django.http import Http404
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from ..model_parts.approvals import ApprovalState, PENDING_REVIEW_STATES
from ..models import (
    AssignmentTarget,
    Meeting,
    MeetingMinutes,
    Report,
    SchoolMembership,
    TeacherAchievementFile,
)
from ..permissions import executive_director_groups, is_executive_director
from ..services_archive import attach_school_consumption_rows

__all__ = ["executive_dashboard"]

# نافذة النشاط التي تُقاس عليها المؤشرات. ثابت واحد حتى لا تختلف بطاقة عن أخرى.
ACTIVITY_WINDOW_DAYS = 30
ACTIVITY_WINDOWS = frozenset({7, 30, 90})


def _subscription_state(school) -> dict:
    """حالة اشتراك المدرسة كما تُعرض في اللوحة.

    مدرسة بلا اشتراك أو باشتراك منتهٍ حالة مشروعة داخل المجموعة، فتُعرض كما هي
    بدل أن تُسقط الصفحة أو تُخفى من الإجمالي.
    """
    subscription = getattr(school, "subscription", None)
    if subscription is None:
        return {"label": "بلا اشتراك", "tone": "none", "days_remaining": None}
    try:
        if subscription.is_expired:
            return {"label": "منتهٍ", "tone": "expired", "days_remaining": 0}
        days = int(subscription.days_remaining or 0)
    except Exception:
        return {"label": "غير معروف", "tone": "none", "days_remaining": None}

    tone = "active" if days > 30 else "ending"
    return {"label": f"{days} يوماً متبقياً", "tone": tone, "days_remaining": days}


def _school_rows(schools, since, previous_since) -> list[dict]:
    """صف لكل مدرسة، بمؤشراتها خلال نافذة النشاط.

    التجميع يتم في استعلامين اثنين لكل المدارس لا استعلام لكل مدرسة، فعدد
    المدارس في المجموعة لا يترجم إلى عدد استعلامات.
    """
    school_ids = [school.pk for school in schools]
    if not school_ids:
        return []

    reports = {
        row["school_id"]: row
        for row in Report.objects.filter(school_id__in=school_ids)
        .values("school_id")
        .annotate(
            total=Count("id"),
            recent=Count("id", filter=Q(created_at__gte=since)),
            previous=Count(
                "id",
                filter=Q(created_at__gte=previous_since, created_at__lt=since),
            ),
            pending=Count(
                "id", filter=Q(approval_state__in=PENDING_REVIEW_STATES)
            ),
        )
    }
    achievements = {
        row["school_id"]: row["total"]
        for row in TeacherAchievementFile.objects.filter(school_id__in=school_ids)
        .values("school_id")
        .annotate(total=Count("id"))
    }
    # منسوبو كل مدرسة — أشخاصاً لا عضويات، فحاملُ دورين ليس شخصين.
    teachers = {
        row["school_id"]: row["total"]
        for row in SchoolMembership.objects.filter(
            school_id__in=school_ids,
            is_active=True,
            role_type__in=SchoolMembership.STAFF_ROLES,
        )
        .values("school_id")
        .annotate(total=Count("teacher_id", distinct=True))
    }

    rows = []
    for school in schools:
        report_row = reports.get(school.pk, {}) or {}
        total_reports = int(report_row.get("total") or 0)
        recent_reports = int(report_row.get("recent") or 0)
        previous_reports = int(report_row.get("previous") or 0)
        rows.append(
            {
                "school": school,
                "teachers": teachers.get(school.pk, 0),
                "reports_total": total_reports,
                "reports_recent": recent_reports,
                "reports_previous": previous_reports,
                "reports_delta": recent_reports - previous_reports,
                "reports_pending": int(report_row.get("pending") or 0),
                "achievements": achievements.get(school.pk, 0),
                "subscription": _subscription_state(school),
                # مُلحق مسبقاً بـ attach_school_consumption_rows، فلا استعلام هنا.
                "consumption": getattr(school, "consumption_row", None) or {
                    "storage": {"percent": 0, "is_unlimited": True, "label": "—"},
                    "seats": {"percent": 0, "is_unlimited": True, "label": "—"},
                    "archive": {"percent": 0, "is_subscribed": False, "label": "—"},
                },
            }
        )
    return rows


@login_required
@require_http_methods(["GET"])
def executive_dashboard(request):
    """لوحة المدير التنفيذي — قراءة مجمَّعة عبر مدارس مجموعته."""
    if not is_executive_director(request.user):
        raise Http404

    groups = list(executive_director_groups(request.user))
    if not groups:
        raise Http404

    requested = (request.GET.get("group") or "").strip()
    group = groups[0]
    if requested:
        # الاختيار مقيَّد بمجموعات المستخدم، فمعرّف من خارجها لا يوسّع الوصول.
        group = next((item for item in groups if str(item.pk) == requested), groups[0])

    schools = list(
        group.schools.filter(is_active=True)
        .select_related("subscription")
        .order_by("name")
    )
    # مدرسة قاربت امتلاء مساحتها ستتوقف فيها عمليات الرفع، والمدير التنفيذي أول
    # من ينبغي أن يعرف. الدالة مشتركة مع دليل المنصة فلا يفترق الرقمان.
    attach_school_consumption_rows(schools)
    now = timezone.now()
    requested_window = (request.GET.get("window") or str(ACTIVITY_WINDOW_DAYS)).strip()
    activity_window_days = (
        int(requested_window)
        if requested_window.isdigit() and int(requested_window) in ACTIVITY_WINDOWS
        else ACTIVITY_WINDOW_DAYS
    )
    since = now - timedelta(days=activity_window_days)
    previous_since = since - timedelta(days=activity_window_days)
    rows = _school_rows(schools, since, previous_since)

    school_ids = [row["school"].pk for row in rows]
    target_qs = AssignmentTarget.objects.filter(
        assignment__group=group,
        school_id__in=school_ids,
    )
    target_by_school = {
        item["school_id"]: item
        for item in target_qs.values("school_id").annotate(
            total=Count("id"),
            done=Count("id", filter=Q(approval_state=ApprovalState.APPROVED)),
            overdue=Count(
                "id",
                filter=Q(
                    assignment__due_at__lt=now,
                    assignment__cancelled_at__isnull=True,
                )
                & ~Q(approval_state=ApprovalState.APPROVED),
            ),
        )
    }

    for row in rows:
        assignment = target_by_school.get(row["school"].pk, {}) or {}
        row["assignments"] = int(assignment.get("total") or 0)
        row["assignments_done"] = int(assignment.get("done") or 0)
        row["assignments_overdue"] = int(assignment.get("overdue") or 0)
        row["completion"] = (
            round(row["assignments_done"] * 100 / row["assignments"])
            if row["assignments"]
            else 0
        )

        risk_score = 0
        risk_reasons = []
        subscription_tone = row["subscription"]["tone"]
        if subscription_tone in {"none", "expired"}:
            risk_score += 50
            risk_reasons.append("الاشتراك متوقف")
        elif subscription_tone == "ending":
            risk_score += 25
            risk_reasons.append("الاشتراك يقترب من الانتهاء")
        if row["consumption"]["storage"]["percent"] >= 90:
            risk_score += 30
            risk_reasons.append("مساحة التخزين حرجة")
        if row["consumption"]["seats"]["percent"] >= 90:
            risk_score += 20
            risk_reasons.append("المقاعد قاربت الامتلاء")
        if row["assignments_overdue"]:
            risk_score += 25
            risk_reasons.append("لديها تكليف متأخر")
        if row["reports_pending"]:
            risk_score += 10
            risk_reasons.append("تقارير تنتظر الاعتماد")
        if not row["reports_recent"]:
            risk_score += 10
            risk_reasons.append("لا نشاط تقارير في الفترة")

        row["risk_score"] = min(risk_score, 100)
        row["risk_reasons"] = risk_reasons
        if row["risk_score"] >= 50:
            row["risk_tone"], row["risk_label"] = "danger", "مرتفعة"
        elif row["risk_score"] >= 20:
            row["risk_tone"], row["risk_label"] = "warning", "متوسطة"
        else:
            row["risk_tone"], row["risk_label"] = "stable", "مستقرة"

    totals = {
        "schools": len(rows),
        "teachers": sum(row["teachers"] for row in rows),
        "reports_total": sum(row["reports_total"] for row in rows),
        "reports_recent": sum(row["reports_recent"] for row in rows),
        "reports_previous": sum(row["reports_previous"] for row in rows),
        "achievements": sum(row["achievements"] for row in rows),
        "assignments": sum(row["assignments"] for row in rows),
        "assignments_done": sum(row["assignments_done"] for row in rows),
        "assignments_overdue": sum(row["assignments_overdue"] for row in rows),
    }
    totals["reports_delta"] = totals["reports_recent"] - totals["reports_previous"]
    totals["completion"] = (
        round(totals["assignments_done"] * 100 / totals["assignments"])
        if totals["assignments"]
        else 0
    )
    # «تحتاج متابعة» يجمع إشارات الخطر في درجة واحدة: الاشتراك والسعة والتأخر
    # والاعتمادات والنشاط. بهذا يبقى ترتيب المدرسة واحداً في البطاقة والجدول.
    needs_attention = sorted(
        (row for row in rows if row["risk_score"] >= 20),
        key=lambda row: (-row["risk_score"], row["school"].name),
    )
    ranked_rows = sorted(
        rows,
        key=lambda row: (
            -row["risk_score"],
            -row["assignments_overdue"],
            -row["reports_recent"],
            row["school"].name,
        ),
    )
    # أعلى عدد تقارير حديثة يُستخدم أساساً لأشرطة المقارنة في القالب.
    busiest = max((row["reports_recent"] for row in rows), default=0)
    for row in rows:
        row["activity_share"] = round(row["reports_recent"] * 100 / busiest) if busiest else 0

    pending_assignment_decisions = (
        target_qs.filter(
            assignment__issuer=request.user,
            approval_state__in=PENDING_REVIEW_STATES,
        )
        .values("assignment_id")
        .distinct()
        .count()
    )
    pending_minutes = MeetingMinutes.objects.filter(
        meeting__group=group,
        meeting__scope=Meeting.Scope.GROUP,
        approval_state__in=PENDING_REVIEW_STATES,
    ).count()
    missing_minutes = Meeting.objects.filter(
        group=group,
        scope=Meeting.Scope.GROUP,
        status=Meeting.Status.HELD,
        minutes__isnull=True,
    ).count()
    decisions_count = pending_assignment_decisions + pending_minutes + missing_minutes
    risk_count = len(needs_attention)

    decision_items = []
    if pending_assignment_decisions:
        decision_items.append(
            {
                "tone": "danger",
                "icon": "fa-stamp",
                "count": pending_assignment_decisions,
                "title": "ردود تكليفات تنتظر قرارك",
                "detail": "راجع ردود المدارس واعتمدها أو أعدها للاستكمال.",
                "url_name": "reports:group_approval_inbox",
            }
        )
    if pending_minutes:
        decision_items.append(
            {
                "tone": "warning",
                "icon": "fa-file-signature",
                "count": pending_minutes,
                "title": "محاضر مجلس بانتظار الاعتماد",
                "detail": "أغلق دورة القرار باعتماد المحاضر الجاهزة.",
                "url_name": "reports:group_approval_inbox",
            }
        )
    if missing_minutes:
        decision_items.append(
            {
                "tone": "warning",
                "icon": "fa-clipboard-question",
                "count": missing_minutes,
                "title": "جلسات منعقدة بلا محاضر",
                "detail": "تابع توثيق مخرجات الجلسات قبل أن تتعطل قراراتها.",
                "url_name": "reports:council_list",
            }
        )
    if totals["assignments_overdue"]:
        decision_items.append(
            {
                "tone": "danger",
                "icon": "fa-clock",
                "count": totals["assignments_overdue"],
                "title": "تكليفات تجاوزت موعدها",
                "detail": "حدّد المدارس المتأخرة واتخذ إجراء المتابعة المناسب.",
                "url_name": "reports:group_assignment_board",
            }
        )
    if risk_count:
        decision_items.append(
            {
                "tone": "warning",
                "icon": "fa-shield-halved",
                "count": risk_count,
                "title": "مدارس تحمل مؤشرات خطر",
                "detail": "ابدأ بالأعلى خطورة حسب الاشتراك والسعة والتأخر والنشاط.",
                "url_name": "reports:group_subscriptions",
            }
        )

    return render(
        request,
        "reports/executive_dashboard.html",
        {
            "group": group,
            "groups": groups,
            "rows": rows,
            "ranked_rows": ranked_rows,
            "totals": totals,
            "needs_attention": needs_attention,
            "decision_items": decision_items,
            "decisions_count": decisions_count,
            "risk_count": risk_count,
            "activity_window_days": activity_window_days,
            "activity_windows": sorted(ACTIVITY_WINDOWS),
            "generated_at": timezone.localtime(now),
            "active": "executive_dashboard",
        },
    )
