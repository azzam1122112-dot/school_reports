# -*- coding: utf-8 -*-
"""«سجل أعمالي» — سجل الإجراءات كما يراه صاحبه.

لماذا صفحة مستقلة عن سجل المدرسة؟ لأن السؤالين مختلفان. المدير يسأل *من فعل
ماذا في مدرستي*، والموظف أو المعلم يسأل *ماذا فعلتُ أنا* — وهو سؤال يسبق
تسليم عمل أو مراجعة مهمة أو الردّ على ملاحظة. تقديم السجل الإداري لغير المدير
يخلط الشأنين، ويكشف نشاط الآخرين على من لا يملك رؤيته.

نطاق هذه الصفحة صارم بحكم بنائها: ``teacher=request.user`` مثبَّتة في الاستعلام
الأساس ولا تُشتق من أي معطى في الطلب، فليس في الصفحة معامل يمكن التلاعب به
للوصول إلى سجل غيره.
"""
from __future__ import annotations

from datetime import timedelta
from itertools import groupby

from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.utils import OperationalError, ProgrammingError
from django.http import HttpRequest, HttpResponse
from django.contrib import messages
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from ..audit_labels import action_filter_choices, attach_views
from ..models import AuditLog
from ._helpers import (
    _clean_query_params,
    _clean_query_value,
    _get_active_school,
    _parse_date_safe,
)

__all__ = ["my_activity_log", "my_work_archive"]

PAGE_SIZE = 30

# نافذة الملخص العلوي. شهر واحد لأنه أقصر مدى يجيب عن «ماذا أنجزت مؤخراً»
# دون أن يتحول العدّاد إلى رقم تراكمي لا معنى له.
SUMMARY_WINDOW_DAYS = 30


def _grouped_by_day(page) -> list[dict]:
    """تجميع صفوف الصفحة الحالية في مجموعات يومية.

    التجميع يقع **بعد** الترقيم لا قبله، فيبقى حجم الصفحة ثابتاً ومتوقعاً.
    التجميع قبله كان يجعل صفحةً تحمل ثلاثة أيام وأخرى ثلاثين حدثاً في يوم واحد.

    ``groupby`` كافٍ لأن الاستعلام مرتّب زمنياً تنازلياً أصلاً، فالأيام متجاورة.
    """
    groups = []
    for day, entries in groupby(page, key=lambda log: timezone.localtime(log.timestamp).date()):
        groups.append({"day": day, "entries": list(entries)})
    return groups


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_activity_log(request: HttpRequest) -> HttpResponse:
    """سجل إجراءات المستخدم الحالي وحده."""
    action = _clean_query_value(request.GET.get("action"))
    start_date = _parse_date_safe(request.GET.get("start_date"))
    end_date = _parse_date_safe(request.GET.get("end_date"))

    allowed_actions = {value for value, _label in AuditLog.Action.choices}
    if action not in allowed_actions:
        action = ""

    logs_qs = None
    total_count = 0
    recent_count = 0
    unavailable = False

    try:
        base_qs = AuditLog.objects.filter(teacher=request.user)
        # العدّادان يُقرآن قبل التصفية عمداً: بطاقات الملخص تصف السجل كله،
        # فلو تبعت الفلتر لتغيّر «إجمالي إجراءاتك» كلما ضيّق المستخدم البحث.
        total_count = base_qs.count()
        since = timezone.now() - timedelta(days=SUMMARY_WINDOW_DAYS)
        recent_count = base_qs.filter(timestamp__gte=since).count()

        logs_qs = base_qs.select_related("school")
        if action:
            logs_qs = logs_qs.filter(action=action)
        if start_date is not None:
            logs_qs = logs_qs.filter(timestamp__date__gte=start_date)
        if end_date is not None:
            logs_qs = logs_qs.filter(timestamp__date__lte=end_date)
    except (OperationalError, ProgrammingError):
        # نفس تحوّط صفحة المدير: بيئة لم تُطبَّق فيها ترحيلات السجل تعرض
        # الصفحة بتنبيه بدل أن تُرجع 500.
        unavailable = True

    if logs_qs is not None:
        page = Paginator(logs_qs, PAGE_SIZE).get_page(request.GET.get("page"))
    else:
        page = Paginator([], PAGE_SIZE).get_page(1)

    attach_views(page)

    has_filters = bool(action or start_date or end_date)

    return render(
        request,
        "reports/my_activity_log.html",
        {
            "active": "my_activity_log",
            "page_obj": page,
            "day_groups": _grouped_by_day(page),
            "actions": action_filter_choices(),
            "q_action": action,
            "q_start": start_date.isoformat() if start_date else "",
            "q_end": end_date.isoformat() if end_date else "",
            "qs": _clean_query_params(request.GET),
            "has_filters": has_filters,
            "total_count": total_count,
            "recent_count": recent_count,
            "summary_window_days": SUMMARY_WINDOW_DAYS,
            "unavailable": unavailable,
        },
    )


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_work_archive(request):
    """أرشيف أعمالي — كل ما أنتجتُه في مكان واحد.

    يجيب عن البند الذي يتكرر في توصيف المعلم والموظف الإداري: «الاطلاع على
    أرشيف أعماله الشخصية». والفرق عن «سجل أعمالي»: ذاك يعرض **ما فعلتَه**
    كأحداث، وهذا يعرض **ما أنتجتَه** كأعمال — والسؤالان مختلفان: الأول
    «متى فعلتُ كذا؟» والثاني «أين ذلك التقرير؟».

    مقسّم بالسنة الدراسية لأنها الوحدة التي يفكّر بها المدرسيون فعلاً، ولأن
    قائمةً بلا تقسيم تصير بعد ثلاث سنوات كشفاً لا يُقلَّب.
    """
    from ..model_parts.approvals import ApprovalState
    from ..models import (
        AssignmentTarget,
        Document,
        Initiative,
        Report,
        TeacherAchievementFile,
    )

    school = _get_active_school(request)
    if school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return redirect("reports:select_school")

    year = _clean_query_value(request.GET.get("year"))

    reports = Report.objects.filter(teacher=request.user, school=school)
    achievements = TeacherAchievementFile.objects.filter(
        teacher=request.user, school=school
    )
    documents = Document.objects.filter(owner=request.user, school=school)
    initiatives = Initiative.objects.filter(teacher=request.user, school=school)
    targets = AssignmentTarget.objects.filter(assignee=request.user).select_related(
        "assignment"
    )

    # السنوات المتاحة تُشتق من أعمال المستخدم نفسه: قائمةٌ تعرض سنواتٍ لا عمل
    # له فيها تجعله يبحث في فراغ.
    years = sorted(
        {value for value in reports.values_list("academic_year", flat=True) if value}
        | {value for value in achievements.values_list("academic_year", flat=True) if value}
        | {value for value in documents.values_list("academic_year", flat=True) if value},
        reverse=True,
    )
    if year and year in years:
        reports = reports.filter(academic_year=year)
        achievements = achievements.filter(academic_year=year)
        documents = documents.filter(academic_year=year)
    else:
        year = ""

    return render(
        request,
        "reports/my_work_archive.html",
        {
            "active": "my_work_archive",
            "active_school": school,
            "years": years,
            "q_year": year,
            "reports": list(reports.select_related("category").order_by("-report_date")[:60]),
            "achievements": list(achievements.order_by("-academic_year")[:20]),
            "documents": list(documents.select_related("department").order_by("-created_at")[:60]),
            "initiatives": list(initiatives.order_by("-created_at")[:30]),
            "targets": list(
                targets.filter(approval_state=ApprovalState.APPROVED).order_by(
                    "-decided_at"
                )[:40]
            ),
            "counts": {
                "reports": reports.count(),
                "achievements": achievements.count(),
                "documents": documents.count(),
                "initiatives": initiatives.count(),
                "targets": targets.filter(approval_state=ApprovalState.APPROVED).count(),
            },
        },
    )
