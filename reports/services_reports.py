# reports/services_reports.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import date
from typing import Optional

from django.core.paginator import EmptyPage, PageNotAnInteger, Paginator
from django.db.models import Q, QuerySet
from django.shortcuts import get_object_or_404

from .models import Report, School

# موديلات مرجعية اختيارية
try:
    from .models import ReportType  # type: ignore
except Exception:  # pragma: no cover
    ReportType = None  # type: ignore

from .permissions import allowed_categories_for, restrict_queryset_for_user


def _model_has_field(model, field_name: str) -> bool:
    try:
        return field_name in {f.name for f in model._meta.get_fields()}
    except Exception:
        return False


def filter_by_school(qs: QuerySet, active_school: Optional[School]) -> QuerySet:
    """تطبيق فلتر المدرسة إن كان للموديل حقل school وكان هناك مدرسة نشطة."""
    if not active_school:
        return qs
    try:
        if _model_has_field(qs.model, "school"):
            return qs.filter(school=active_school)
    except Exception:
        return qs
    return qs


def paginate(qs: QuerySet, *, per_page: int, page: str | int | None):
    paginator = Paginator(qs, per_page)
    try:
        return paginator.page(page or 1)
    except PageNotAnInteger:
        return paginator.page(1)
    except EmptyPage:
        return paginator.page(paginator.num_pages)


def get_teacher_reports_queryset(*, user, active_school: Optional[School]) -> QuerySet:
    qs = (
        Report.objects.select_related("teacher", "category")
        .filter(teacher=user)
        .order_by("-report_date", "-id")
    )
    return filter_by_school(qs, active_school)


def apply_teacher_report_filters(
    qs: QuerySet,
    *,
    start_date,
    end_date,
    q: str,
) -> QuerySet:
    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)
    if q:
        qs = qs.filter(Q(title__icontains=q) | Q(idea__icontains=q))
    return qs


def teacher_report_stats(qs: QuerySet) -> dict:
    today = date.today()
    return {
        "total": qs.count(),
        "this_month": qs.filter(report_date__month=today.month, report_date__year=today.year).count(),
    }


def get_admin_reports_queryset(*, user, active_school: Optional[School]) -> QuerySet:
    qs = Report.objects.select_related("teacher", "category").order_by("-report_date", "-id")
    qs = restrict_queryset_for_user(qs, user, active_school)
    return filter_by_school(qs, active_school)


def apply_admin_report_filters(
    qs: QuerySet,
    *,
    start_date,
    end_date,
    teacher_name: str,
    category: str,
    cats,
) -> QuerySet:
    if start_date:
        qs = qs.filter(report_date__gte=start_date)
    if end_date:
        qs = qs.filter(report_date__lte=end_date)

    if teacher_name:
        for token in [t for t in teacher_name.split() if t]:
            qs = qs.filter(teacher_name__icontains=token)

    if category:
        category = (category or "").strip().lower()
        if cats and "all" not in cats:
            if category in cats:
                qs = qs.filter(category__code=category)
        else:
            qs = qs.filter(category__code=category)

    return qs


def get_reporttype_choices(*, active_school: Optional[School]) -> list[tuple[str, str]]:
    if ReportType is None:
        return []

    qs = ReportType.objects.filter(is_active=True).order_by("order", "name")
    try:
        if active_school is not None and _model_has_field(ReportType, "school"):
            qs = qs.filter(school=active_school)
    except Exception:
        pass

    return [(rt.code, rt.name) for rt in qs]


def get_report_for_user_or_404(*, user, pk: int, active_school: Optional[School]):
    """جلب تقرير واحد مع احترام عزل المدارس وصلاحيات الرؤية."""
    qs = Report.objects.select_related("teacher", "category")

    if active_school is not None and _model_has_field(Report, "school"):
        qs = qs.filter(school=active_school)

    if getattr(user, "is_staff", False):
        return get_object_or_404(qs, pk=pk)

    try:
        cats = allowed_categories_for(user, active_school) or set()
    except Exception:
        cats = set()

    if "all" in cats:
        return get_object_or_404(qs, pk=pk)

    if cats:
        return get_object_or_404(qs.filter(Q(teacher=user) | Q(category__code__in=list(cats))), pk=pk)

    return get_object_or_404(qs, pk=pk, teacher=user)
