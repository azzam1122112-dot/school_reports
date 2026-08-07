# -*- coding: utf-8 -*-
"""صندوق المراجعة والاعتماد.

صندوق واحد للوكيل والمدير معاً، لا صندوقان. السبب أن ما يفرّق بينهما ليس
الشاشة بل ما يملكانه فيها: الوكيل يرى تقارير نطاقه ويوصي، والمدير يرى كل شيء
ويعتمد — والفرق كله يخرج من ``available_actions``. وشاشتان تعرضان القائمة
نفسها بأزرار مختلفة تتباعدان عند أول تعديل.

كل إجراء يمر عبر ``services_approval`` ولا شيء يُقرَّر هنا: هذا العرض يترجم
النقرة إلى نداء، ويترجم الخطأ إلى رسالة. ولذلك لا يُوجد في هذا الملف شرطُ
صلاحية واحد — وذلك مقصود.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from .. import capabilities as caps
from ..model_parts.approvals import ApprovalState, PENDING_REVIEW_STATES
from ..models import Report
from ..permissions import (
    capability_source,
    is_school_manager,
    supervised_department_ids,
)
from ..services_approval import (
    ACTION_DISPATCH,
    ApprovalError,
    available_actions,
    transitions_for,
)
from ._helpers import *  # noqa: F401,F403
from ._helpers import _get_active_school

__all__ = ["approval_inbox", "approval_detail", "approval_action"]

# ترتيب العرض: ما ينتظر قراراً منك أولاً، ثم ما ينتظر غيرك.
_STATE_ORDER = {
    ApprovalState.RECOMMENDED: 0,
    ApprovalState.SUBMITTED: 1,
    ApprovalState.UNDER_REVIEW: 2,
    ApprovalState.NEEDS_INFO: 3,
    ApprovalState.RETURNED: 4,
}


def _reviewable_reports(user, school):
    """التقارير التي يحقّ لهذا المستخدم رؤيتها في الصندوق.

    المدير يرى كل ما ينتظر في مدرسته. وغيره يرى ما يقع في نطاقه وحده — والنطاق
    الفارغ يعني لا شيء، لا كل شيء.
    """
    base = (
        Report.objects.filter(school=school, approval_state__in=PENDING_REVIEW_STATES)
        .select_related("teacher", "category", "reviewed_by")
        .order_by("-submitted_at", "-id")
    )

    if is_school_manager(user, active_school=school):
        return base

    if capability_source(user, caps.REVIEW_REPORTS, school) is None:
        return base.none()

    supervised = supervised_department_ids(user, school)
    if not supervised:
        return base.none()

    return base.filter(category__departments__id__in=supervised).distinct()


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def approval_inbox(request):
    """قائمة ما ينتظر مراجعة أو اعتماداً."""
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    is_manager = is_school_manager(request.user, active_school=active_school)
    may_review = capability_source(request.user, caps.REVIEW_REPORTS, active_school) is not None
    if not (is_manager or may_review or request.user.is_superuser):
        messages.error(request, "لا تملك صلاحية الوصول إلى هذه الصفحة.")
        return redirect("reports:home")

    state_filter = (request.GET.get("state") or "").strip()
    reports = _reviewable_reports(request.user, active_school)
    if state_filter in {value for value, _ in ApprovalState.choices}:
        reports = reports.filter(approval_state=state_filter)
    else:
        state_filter = ""

    now = timezone.now()
    rows = []
    for report in reports[:200]:
        actions = available_actions(report, request.user, school=active_school)
        # عمر الانتظار يُحسب هنا لا في القالب: صندوقٌ يعرض عشرين عملاً بلا
        # تمييزٍ بين ما وصل اليوم وما ينتظر منذ أسبوعين يُقرأ بالترتيب لا
        # بالأولوية — فيُنسى الأقدم لأنه في الأسفل.
        waiting_days = (
            (now - report.submitted_at).days if report.submitted_at else None
        )
        rows.append(
            {
                "report": report,
                "actions": actions,
                "order": _STATE_ORDER.get(report.approval_state, 9),
                "waiting_days": waiting_days,
                # «قرارك» = ما ينتهي عندك، تمييزاً عمّا تراه وتنتظر فيه غيرك.
                "is_mine": bool({"approve", "recommend"} & set(actions)),
            }
        )
    # ما ينتهي عندك أولاً، ثم بترتيب الحالة. و``regroup`` في القالب يجمع
    # المتجاورَ وحده، فالفرز بـ ``is_mine`` شرطُ صحّته لا تحسينُ عرض.
    rows.sort(
        key=lambda row: (not row["is_mine"], row["order"], -(row["report"].pk or 0))
    )

    mine_count = sum(1 for row in rows if row["is_mine"])
    oldest_days = max(
        (row["waiting_days"] for row in rows if row["waiting_days"] is not None),
        default=0,
    )

    counts = {
        state: sum(1 for row in rows if row["report"].approval_state == state)
        for state in PENDING_REVIEW_STATES
    }

    return render(
        request,
        "reports/approval_inbox.html",
        {
            "active": "approval_inbox",
            "active_school": active_school,
            "rows": rows,
            "counts": counts,
            "total": len(rows),
            "mine_count": mine_count,
            "oldest_days": oldest_days,
            "state_filter": state_filter,
            "is_manager": is_manager,
            "states": ApprovalState.choices,
        },
    )


def _report_for_review(request, pk: int, school):
    """التقرير مع فحص أنه ضمن ما يراه هذا المستخدم — لا مجرد أنه موجود."""
    report = get_object_or_404(
        Report.objects.select_related("teacher", "category", "school"),
        pk=pk,
        school=school,
    )
    is_owner = report.teacher_id == request.user.pk
    if is_owner or is_school_manager(request.user, active_school=school):
        return report
    if _reviewable_reports(request.user, school).filter(pk=pk).exists():
        return report
    # سجل خارج النطاق يُعامَل كغير موجود: تمييز «ممنوع» عن «غير موجود» يكشف
    # وجود التقرير لمن لا يحق له معرفة أنه موجود أصلاً.
    raise Http404


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def approval_detail(request, pk: int):
    """تفصيل تقرير واحد مع تاريخ اعتماده الكامل."""
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    report = _report_for_review(request, pk, active_school)

    return render(
        request,
        "reports/approval_detail.html",
        {
            "active": "approval_inbox",
            "active_school": active_school,
            "report": report,
            "actions": available_actions(report, request.user, school=active_school),
            "timeline": list(transitions_for(report)),
            "is_owner": report.teacher_id == request.user.pk,
        },
    )


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def approval_action(request, pk: int):
    """تنفيذ إجراء اعتماد واحد.

    الإجراء يُنفَّذ بـ ``ACTION_DISPATCH`` بعد التحقق أنه ضمن ما تعرضه
    ``available_actions`` — فحصان لا واحد: الأول يمنع اسم إجراء ملفَّق، والثاني
    داخل الخدمة يمنع الإجراء المشروع في الحالة الخاطئة.
    """
    active_school = _get_active_school(request)
    if active_school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    report = _report_for_review(request, pk, active_school)
    action = (request.POST.get("approval_action") or "").strip()
    note = (request.POST.get("note") or "").strip()
    next_url = (request.POST.get("next") or "").strip()

    handler = ACTION_DISPATCH.get(action)
    if handler is None or action not in available_actions(
        report, request.user, school=active_school
    ):
        messages.error(request, "هذا الإجراء غير متاح على هذا التقرير الآن.")
        return redirect("reports:approval_detail", pk=pk)

    try:
        handler(report, request.user, school=active_school, note=note)
    except PermissionDenied as exc:
        messages.error(request, str(exc) or "لا تملك هذا الإجراء.")
    except (ApprovalError, ValidationError) as exc:
        detail = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, detail[0])
    else:
        messages.success(
            request,
            {
                "submit": "أُرسل التقرير للمراجعة.",
                "withdraw": "سُحب التقرير للتعديل.",
                "start_review": "بدأت مراجعة التقرير.",
                "request_info": "طُلب استكمال البيانات من مُعدّ التقرير.",
                "return": "أُعيد التقرير لمُعدّه مع ملاحظتك.",
                "recommend": "رُفع التقرير للمدير موصىً باعتماده.",
                "approve": "اعتُمد التقرير.",
            }.get(action, "نُفِّذ الإجراء."),
        )

    if next_url == "inbox":
        return redirect("reports:approval_inbox")
    return redirect("reports:approval_detail", pk=pk)
