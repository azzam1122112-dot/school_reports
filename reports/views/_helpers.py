# reports/views/_helpers.py
# -*- coding: utf-8 -*-
"""Shared imports, helpers and constants for all view modules."""
from __future__ import annotations

from datetime import date, timedelta
import logging
import os
import traceback
from typing import Optional, Tuple
from urllib.parse import quote, urlencode, urlparse

import openpyxl

from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth import authenticate, login, logout, update_session_auth_hash
from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.hashers import make_password
from django.core.cache import cache
from django.core.paginator import Paginator
from django.db import IntegrityError, transaction
from django.db.models import (
    Count,
    Exists,
    F,
    Prefetch,
    Q,
    ManyToManyField,
    ForeignKey,
    OuterRef,
    Subquery,
    Sum,
)
from django.db.models.functions import TruncWeek, TruncMonth
from django.core.exceptions import ValidationError
from django.http import FileResponse, Http404, HttpRequest, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.utils.http import url_has_allowed_host_and_scheme
from django.utils.safestring import mark_safe
from django.views.decorators.http import require_http_methods
from django.views.decorators.cache import cache_control, never_cache
from django.db.models.deletion import ProtectedError

from django.templatetags.static import static
from django.contrib.staticfiles import finders

from django_ratelimit.decorators import ratelimit


def _user_guide_md_path() -> str:
    # Prefer the curated precise guide if present, fallback to legacy guide.
    preferred = os.path.join(settings.BASE_DIR, "docs", "system_user_guide_precise_ar.md")
    if os.path.exists(preferred):
        return preferred
    return os.path.join(settings.BASE_DIR, "docs", "user_guide_complete_ar.md")


@require_http_methods(["GET"])
def user_guide(request: HttpRequest) -> HttpResponse:
    """Public HTML page rendering the Arabic user guide Markdown."""

    md_path = _user_guide_md_path()
    if not os.path.exists(md_path):
        raise Http404("User guide not found")

    with open(md_path, "r", encoding="utf-8") as fp:
        md_text = fp.read()

    # Remove the first top-level title to avoid duplication with the page header.
    try:
        lines = md_text.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and lines[0].startswith("# "):
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
        md_text = "\n".join(lines)
    except Exception:
        pass

    try:
        import markdown as md
    except Exception:
        return HttpResponse("Markdown renderer is not installed.", status=500, content_type="text/plain")

    guide_html = md.markdown(
        md_text,
        extensions=["extra", "fenced_code", "tables"],
        output_format="html5",
    )

    ctx = {
        "guide_html": mark_safe(guide_html),
        "download_url": reverse("reports:user_guide_download"),
        "download_pdf_url": reverse("reports:user_guide_download_pdf"),
    }
    return render(request, "reports/user_guide.html", ctx)


@require_http_methods(["GET"])
def user_guide_download(request: HttpRequest) -> HttpResponse:
    """Download the raw Markdown file for the user guide."""

    md_path = _user_guide_md_path()
    if not os.path.exists(md_path):
        raise Http404("User guide not found")

    return FileResponse(
        open(md_path, "rb"),
        as_attachment=True,
        filename="user_guide_complete_ar.md",
        content_type="text/markdown; charset=utf-8",
    )


@require_http_methods(["GET"])
def user_guide_download_pdf(request: HttpRequest) -> HttpResponse:
    """Download the user guide as a PDF (includes platform logo)."""

    md_path = _user_guide_md_path()
    if not os.path.exists(md_path):
        raise Http404("User guide not found")

    with open(md_path, "r", encoding="utf-8") as fp:
        md_text = fp.read()

    # Remove the first top-level title to avoid duplication with the PDF header.
    try:
        lines = md_text.splitlines()
        while lines and not lines[0].strip():
            lines.pop(0)
        if lines and lines[0].startswith("# "):
            lines.pop(0)
            while lines and not lines[0].strip():
                lines.pop(0)
        md_text = "\n".join(lines)
    except Exception:
        pass

    try:
        import markdown as md
    except Exception:
        return HttpResponse("Markdown renderer is not installed.", status=500, content_type="text/plain")

    guide_html = md.markdown(
        md_text,
        extensions=["extra", "fenced_code", "tables"],
        output_format="html5",
    )

    logo_src = None
    try:
        import base64

        fpath = finders.find("img/logo1.png")
        if fpath:
            with open(fpath, "rb") as f:
                b64 = base64.b64encode(f.read()).decode("ascii")
            logo_src = f"data:image/png;base64,{b64}"
    except Exception:
        logo_src = None

    if not logo_src:
        # Fallback to absolute URL (works on Linux/production when static is served).
        logo_src = request.build_absolute_uri(static("img/logo1.png"))
    html = render_to_string(
        "reports/user_guide_pdf.html",
        {
            "title": "دليل المستخدم الشامل — منصة توثيق",
            "logo_url": logo_src,
            "guide_html": mark_safe(guide_html),
        },
        request=request,
    )

    try:
        from weasyprint import HTML
    except Exception:
        logging.getLogger(__name__).exception("WeasyPrint is not available for PDF rendering")
        return HttpResponse(
            "تعذر توليد ملف PDF على هذا الخادم حاليًا. شغّل المشروع على Docker/Render (Linux) أو ثبّت مكتبات WeasyPrint على Windows.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )

    try:
        pdf_bytes = HTML(string=html, base_url=request.build_absolute_uri("/")).write_pdf()
    except Exception:
        logging.getLogger(__name__).exception("Failed to render user guide PDF")
        return HttpResponse(
            "تعذر توليد ملف PDF حاليًا.",
            status=503,
            content_type="text/plain; charset=utf-8",
        )
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = 'attachment; filename="user_guide.pdf"'
    return response

# ===== فورمات =====
from ..forms import (
    ReportForm,
    TeacherCreateForm,
    TeacherEditForm,
    MyProfilePhoneForm,
    MyPasswordChangeForm,
    TicketActionForm,
    TicketCreateForm,
    DepartmentForm,  # إن لم تكن موجودة في مشروعك سيتم استخدام بديل داخلي
    ManagerCreateForm,
    SubscriptionPlanForm,
    SchoolSubscriptionForm,
    AchievementCreateYearForm,
    TeacherAchievementFileForm,
    AchievementSectionNotesForm,
    AchievementEvidenceUploadForm,
    AchievementManagerNotesForm,
    PlatformAdminCreateForm,
    PlatformSchoolNotificationForm,
    PrivateCommentForm,
    TicketNoteEditForm,
)

# إشعارات (اختياري)
try:
    from ..forms import NotificationCreateForm  # type: ignore
except Exception:
    NotificationCreateForm = None  # type: ignore

# ===== موديلات =====
from ..models import (
    Report,
    PlatformSettings,
    ShareLink,
    get_share_link_default_days,
    Teacher,
    Ticket,
    TicketNote,
    TicketImage,
    Role,
    School,
    SchoolMembership,
    MANAGER_SLUG,
    SubscriptionPlan,
    SchoolSubscription,
    Payment,
    AuditLog,
    TeacherAchievementFile,
    AchievementSection,
    AchievementEvidenceImage,
    AchievementEvidenceReport,
    TeacherPrivateComment,
)

from ..services_achievement import (
    achievement_picker_reports_qs,
    add_report_evidence,
    freeze_achievement_report_evidences,
    remove_report_evidence,
)

# موديلات الإشعارات (اختياري)
try:
    from ..models import Notification, NotificationRecipient  # type: ignore
except Exception:
    Notification = None  # type: ignore
    NotificationRecipient = None  # type: ignore

# موديلات مرجعية اختيارية
try:
    from ..models import ReportType  # type: ignore
except Exception:  # pragma: no cover
    ReportType = None  # type: ignore

try:
    from ..models import Department  # type: ignore
except Exception:  # pragma: no cover
    Department = None  # type: ignore

try:
    from ..models import DepartmentMembership  # type: ignore
except Exception:  # pragma: no cover
    DepartmentMembership = None  # type: ignore

# ===== صلاحيات =====
from ..permissions import (
    allowed_categories_for,
    role_required,
    restrict_queryset_for_user,
    is_platform_admin,
    platform_allowed_schools_qs,
    platform_can_access_school,
)
try:
    from ..permissions import is_officer  # type: ignore
except Exception:
    # بديل مرن إن لم تتوفر الدالة في permissions
    def is_officer(user) -> bool:
        try:
            if not getattr(user, "is_authenticated", False):
                return False
            from ..models import DepartmentMembership  # import محلي
            role_type = getattr(DepartmentMembership, "OFFICER", "officer")
            return DepartmentMembership.objects.filter(
                teacher=user, role_type=role_type, department__is_active=True
            ).exists()
        except Exception:
            return False

# ===== خدمات التقارير (تنظيم منطق العرض/التصفية) =====
from ..services_reports import (
    apply_admin_report_filters,
    apply_teacher_report_filters,
    get_admin_reports_queryset,
    get_report_for_user_or_404 as svc_get_report_for_user_or_404,
    get_reporttype_choices,
    get_teacher_reports_queryset,
    paginate as svc_paginate,
    teacher_report_stats,
)

from ..permissions import (
    can_delete_report,
    can_edit_report,
    can_share_report,
)

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
    return bool(
        getattr(user, "is_authenticated", False)
        and (_is_staff(user) or is_officer(user) or is_platform_admin(user))
    )


def _safe_next_url(next_url: str | None) -> str | None:
    if not next_url:
        return None
    next_url = (next_url or "").strip()
    if not next_url:
        return None
    # حماية من قيم template الشائعة عند وجود None
    if next_url.lower() in {"none", "null", "undefined"}:
        return None

    # نسمح فقط بمسارات داخلية تبدأ بـ / (ونمنع //)
    if not next_url.startswith("/") or next_url.startswith("//"):
        return None

    parsed = urlparse(next_url)
    if parsed.scheme == "" and parsed.netloc == "":
        return next_url
    return None


def _role_display_map(active_school: Optional[School] = None) -> dict:
    """خريطة عرض عربية للأدوار/الأقسام.

    ملاحظة مهمة للتوسع (Multi-tenant): قد تتكرر slugs للأقسام بين المدارس،
    لذا عندما تتوفر مدرسة نشطة نُقيّد القراءة عليها (مع السماح بالأقسام العامة school=NULL).
    """
    base = {"teacher": "المعلم", "manager": "المدير", "officer": "مسؤول قسم"}
    if Department is not None:
        try:
            qs = Department.objects.filter(is_active=True).only("slug", "role_label", "name")
            if active_school is not None and _model_has_field(Department, "school"):
                qs = qs.filter(Q(school=active_school) | Q(school__isnull=True))
            for d in qs:
                base[d.slug] = d.role_label or d.name or d.slug
        except Exception:
            pass
    return base


def _is_manager_in_school(user, active_school: Optional[School]) -> bool:
    """هل المستخدم مدير داخل المدرسة النشطة؟

    - السوبر: نعم
    - role.slug == manager: نعم (توافق خلفي)
    - أو SchoolMembership(RoleType.MANAGER) داخل active_school
    """
    if getattr(user, "is_superuser", False):
        return True
    try:
        if getattr(getattr(user, "role", None), "slug", None) == "manager":
            return True
    except Exception:
        pass

    if active_school is None:
        return False
    try:
        return SchoolMembership.objects.filter(
            teacher=user,
            school=active_school,
            role_type=SchoolMembership.RoleType.MANAGER,
            is_active=True,
        ).exists()
    except Exception:
        return False


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


def _private_comment_role_label(author, school: Optional[School]) -> str:
    return _canonical_role_label(author, school)


def _school_manager_label(school: Optional[School]) -> str:
    """مسمى مدير/مديرة المدرسة حسب نوع المدرسة."""
    gender = (getattr(school, "gender", "") or "").strip().lower()
    girls_value = str(getattr(getattr(School, "Gender", None), "GIRLS", "girls")).strip().lower()
    return "مديرة المدرسة" if gender == girls_value else "مدير المدرسة"


def _school_teacher_label(school: Optional[School]) -> str:
    """مسمى معلم/معلمة حسب نوع المدرسة."""
    gender = (getattr(school, "gender", "") or "").strip().lower()
    girls_value = str(getattr(getattr(School, "Gender", None), "GIRLS", "girls")).strip().lower()
    return "المعلمة" if gender == girls_value else "المعلم"


def _school_teachers_obj_label(school: Optional[School]) -> str:
    """صيغة جمع منصوبة/مجرورة (المعلمين/المعلمات) حسب نوع المدرسة."""
    gender = (getattr(school, "gender", "") or "").strip().lower()
    girls_value = str(getattr(getattr(School, "Gender", None), "GIRLS", "girls")).strip().lower()
    return "المعلمات" if gender == girls_value else "المعلمين"


def _canonical_role_label(user, school: Optional[School]) -> str:
    """إرجاع تسمية دور موحّدة (بدون تداخلات).

    الأدوار المعتمدة فقط:
    - معلم
    - مدير المدرسة
    - المشرف العام
    - مدير النظام
    """
    if user is None:
        return ""

    # مدير النظام (الأعلى أولوية): سوبر يوزر دائمًا
    try:
        if getattr(user, "is_superuser", False):
            return "مدير النظام"
    except Exception:
        pass

    # المشرف العام
    try:
        if is_platform_admin(user) or getattr(user, "is_platform_admin", False):
            scope = getattr(user, "platform_scope", None)
            role_obj = getattr(scope, "role", None) if scope is not None else None
            role_name = (getattr(role_obj, "name", "") or "").strip()
            return role_name or "المشرف العام"
    except Exception:
        pass

    # مدير المدرسة
    try:
        if school is not None and _is_manager_in_school(user, school):
            return _school_manager_label(school)
    except Exception:
        pass
    try:
        role = getattr(user, "role", None)
        if role is not None and (getattr(role, "slug", "") or "").strip().lower() == "manager":
            return _school_manager_label(school)
    except Exception:
        pass

    # مدير النظام (is_staff فقط) — لا نستخدم _is_staff هنا لأنه يُعيد True لمدير المدرسة
    try:
        if getattr(user, "is_staff", False):
            return "مدير النظام"
    except Exception:
        pass

    return _school_teacher_label(school)


def _canonical_sender_name(user) -> str:
    if user is None:
        return "الإدارة"
    return (
        (getattr(user, "name", None) or "").strip()
        or (getattr(user, "phone", None) or "").strip()
        or (getattr(user, "username", None) or "").strip()
        or "الإدارة"
    )


def _model_has_field(model, field_name: str) -> bool:
    """تحقق آمن: هل الموديل يحتوي على حقل باسم معين؟"""
    if model is None:
        return False
    try:
        return field_name in {f.name for f in model._meta.get_fields()}
    except Exception:
        return False


def _get_active_school(request: HttpRequest) -> Optional[School]:
    """إرجاع المدرسة المختارة حالياً من الجلسة (إن وُجدت).

    تحسين احترافي:
    - إذا لم تُحدَّد مدرسة في الجلسة، وكان للمستخدم مدرسة واحدة فقط → نعتبرها المدرسة النشطة تلقائياً.
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

    # توافق خلفي: بعض الحسابات القديمة تعتمد على Role(slug='manager')
    role_slug = None
    try:
        role_slug = getattr(getattr(user, "role", None), "slug", None)
    except Exception:
        role_slug = None

    try:
        if role_slug == "manager":
            qs = (
                School.objects.filter(
                    memberships__teacher=user,
                    memberships__is_active=True,
                    is_active=True,
                )
                .distinct()
                .order_by("name")
            )
        else:
            qs = (
                School.objects.filter(
                    memberships__teacher=user,
                    memberships__role_type=SchoolMembership.RoleType.MANAGER,
                    memberships__is_active=True,
                    is_active=True,
                )
                .distinct()
                .order_by("name")
            )
        return list(qs)
    except Exception:
        return []


def _user_department_codes(user, active_school: Optional[School] = None) -> list[str]:
    codes = set()

    # في وضع تعدد المدارس، يجب تحديد المدرسة النشطة لتجنب تداخل slugs بين المدارس
    try:
        if active_school is None and School.objects.filter(is_active=True).count() > 1:
            return []
    except Exception:
        # fail-closed إذا تعذر تحديد عدد المدارس
        if active_school is None:
            return []

    if DepartmentMembership is not None:
        try:
            mem_qs = DepartmentMembership.objects.filter(teacher=user)
            if active_school is not None:
                mem_qs = mem_qs.filter(department__school=active_school)
            mem_codes = mem_qs.values_list("department__slug", flat=True)
            for c in mem_codes:
                if c:
                    codes.add(c)
        except Exception:
            logger.exception("Failed to fetch user department codes")

    return list(codes)


def _is_report_viewer(user, active_school: Optional[School] = None) -> bool:
    """(تم إلغاء دور مشرف التقارير)"""
    return False


def _ensure_achievement_sections(ach_file: TeacherAchievementFile) -> None:
    """يضمن وجود 11 محورًا ثابتًا داخل الملف."""
    existing = set(
        AchievementSection.objects.filter(file=ach_file).values_list("code", flat=True)
    )
    to_create = []
    for code, title in AchievementSection.Code.choices:
        if int(code) in existing:
            continue
        to_create.append(
            AchievementSection(file=ach_file, code=int(code), title=str(title))
        )
    if to_create:
        AchievementSection.objects.bulk_create(to_create)
