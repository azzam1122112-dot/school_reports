# reports/pdf_report.py
# -*- coding: utf-8 -*-
"""توليد PDF لتقرير واحد بإعادة استخدام قالب الطباعة الرسمي (report_print.html).

يُستخدم في تصدير/أرشفة بيانات المدرسة لإدراج كل تقرير كملف PDF مُنسّق
(ترويسة رسمية + جدول بيانات + الوصف + الصور + التواقيع)، بدل الاكتفاء بالصور.
"""
from __future__ import annotations

from typing import Tuple

from django.conf import settings
from django.template.loader import render_to_string
from django.templatetags.static import static

from .models import SchoolMembership
from .utils import _resolve_department_for_category, _build_head_decision


def _school_principal_name(school) -> str:
    if school is None:
        return getattr(settings, "SCHOOL_PRINCIPAL", "") or ""
    try:
        membership = (
            SchoolMembership.objects.select_related("teacher")
            .filter(school=school, role_type=SchoolMembership.RoleType.MANAGER, is_active=True)
            .order_by("-id")
            .first()
        )
        if membership and membership.teacher:
            return getattr(membership.teacher, "name", "") or ""
    except Exception:
        pass
    return getattr(settings, "SCHOOL_PRINCIPAL", "") or ""


def _moe_logo_url() -> str:
    moe_logo_url = (getattr(settings, "MOE_LOGO_URL", "") or "").strip()
    if not moe_logo_url:
        try:
            path = (getattr(settings, "MOE_LOGO_STATIC", "") or "").strip()
            if path:
                moe_logo_url = static(path)
        except Exception:
            moe_logo_url = ""
    if not moe_logo_url:
        moe_logo_url = static("img/UntiTtled-1.png")
    return moe_logo_url


def build_report_print_context(report) -> dict:
    """يبني نفس سياق report_print.html (لكن بوضع PDF وبدون التعليقات الخاصة)."""
    school = getattr(report, "school", None)

    dept = None
    try:
        dept = _resolve_department_for_category(getattr(report, "category", None), school)
        if dept is not None:
            dept_school = getattr(dept, "school", None)
            if dept_school is not None and dept_school != school:
                dept = None
    except Exception:
        dept = None

    school_stage = ""
    if school is not None:
        try:
            school_stage = getattr(school, "get_stage_display", lambda: "")() or ""
        except Exception:
            school_stage = getattr(school, "stage", "") or ""

    return {
        "r": report,
        "head_decision": _build_head_decision(dept),
        "SCHOOL_PRINCIPAL": _school_principal_name(school),
        "SCHOOL_NAME": getattr(school, "name", "") if school else getattr(settings, "SCHOOL_NAME", "منصة التقارير المدرسية"),
        "SCHOOL_STAGE": school_stage,
        "SCHOOL_LOGO_URL": "",
        "MOE_LOGO_URL": _moe_logo_url(),
        "show_comments": False,
        "for_pdf": True,
    }


def generate_report_pdf(*, request, report) -> Tuple[bytes, str]:
    """يولّد PDF لتقرير ويعيد (bytes, filename).

    - يعيد استخدام قالب الطباعة الرسمي لضمان تطابق الشكل مع طباعة المتصفح.
    - WeasyPrint يطبّق أنماط ``@media print`` فيخفي شريط الأدوات والتعليقات تلقائيًا.
    """
    html = render_to_string("reports/report_print.html", build_report_print_context(report))

    from weasyprint import HTML  # داخل الدالة لتفادي فشل الاستيراد في بيئات بلا مكتبات النظام

    base_url = None
    try:
        base_url = request.build_absolute_uri("/")
    except Exception:
        base_url = None

    pdf_bytes = HTML(string=html, base_url=base_url).write_pdf()
    filename = f"report_{getattr(report, 'id', 'x')}.pdf"
    return pdf_bytes, filename
