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

from ..models import Report, SchoolMembership, TeacherAchievementFile
from ..permissions import executive_director_groups, is_executive_director
from ..services_archive import attach_school_consumption_rows

__all__ = ["executive_dashboard"]

# نافذة النشاط التي تُقاس عليها المؤشرات. ثابت واحد حتى لا تختلف بطاقة عن أخرى.
ACTIVITY_WINDOW_DAYS = 30


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


def _school_rows(schools, since) -> list[dict]:
    """صف لكل مدرسة، بمؤشراتها خلال نافذة النشاط.

    التجميع يتم في استعلامين اثنين لكل المدارس لا استعلام لكل مدرسة، فعدد
    المدارس في المجموعة لا يترجم إلى عدد استعلامات.
    """
    school_ids = [school.pk for school in schools]
    if not school_ids:
        return []

    reports = {
        row["school_id"]: (row["total"], row["recent"])
        for row in Report.objects.filter(school_id__in=school_ids)
        .values("school_id")
        .annotate(
            total=Count("id"),
            recent=Count("id", filter=Q(created_at__gte=since)),
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
        total_reports, recent_reports = reports.get(school.pk, (0, 0))
        rows.append(
            {
                "school": school,
                "teachers": teachers.get(school.pk, 0),
                "reports_total": total_reports,
                "reports_recent": recent_reports,
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
    since = timezone.now() - timedelta(days=ACTIVITY_WINDOW_DAYS)
    rows = _school_rows(schools, since)

    totals = {
        "schools": len(rows),
        "teachers": sum(row["teachers"] for row in rows),
        "reports_total": sum(row["reports_total"] for row in rows),
        "reports_recent": sum(row["reports_recent"] for row in rows),
        "achievements": sum(row["achievements"] for row in rows),
    }
    # «تحتاج متابعة» يجمع الآن سببين: اشتراك يقارب الانتهاء، أو سعة تقارب
    # الامتلاء — فامتلاء المساحة يوقف رفع الملفات كما يوقفه انتهاء الاشتراك.
    needs_attention = [
        row
        for row in rows
        if row["subscription"]["tone"] in {"none", "expired", "ending"}
        or row["consumption"]["storage"]["percent"] >= 90
        or row["consumption"]["seats"]["percent"] >= 90
    ]
    # أعلى عدد تقارير حديثة يُستخدم أساساً لأشرطة المقارنة في القالب.
    busiest = max((row["reports_recent"] for row in rows), default=0)
    for row in rows:
        row["activity_share"] = round(row["reports_recent"] * 100 / busiest) if busiest else 0

    return render(
        request,
        "reports/executive_dashboard.html",
        {
            "group": group,
            "groups": groups,
            "rows": rows,
            "totals": totals,
            "needs_attention": needs_attention,
            "activity_window_days": ACTIVITY_WINDOW_DAYS,
            "active": "executive_dashboard",
        },
    )
