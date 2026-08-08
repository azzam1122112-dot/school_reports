# reports/models.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import timedelta
from typing import Optional, TYPE_CHECKING
import secrets
import os

from urllib.parse import quote

from django.conf import settings
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator, FileExtensionValidator
from django.db import models, transaction
from django.db.models.signals import post_migrate, post_save
from django.dispatch import receiver
from django.utils.text import get_valid_filename, slugify
from django.utils import timezone

# تخزين المرفقات (R2 أو محلي)
from ..storage import PublicRawMediaStorage
from ..validators import validate_attachment_file, validate_circular_attachment_file, validate_image_file, validate_pdf_file

if TYPE_CHECKING:
    from .achievements import AchievementEvidenceImage, TeacherAchievementFile
    from .billing import Payment
    from .notifications import Notification, TicketImage
    from .reports import Report
    from .schools import School
    from .tickets import Ticket

# =========================
# ثوابت عامة
# =========================
MANAGER_SLUG = "manager"
MANAGER_NAME = "الإدارة"
MANAGER_ROLE_LABEL = "المدير"


def _normalize_academic_year_hijri(value: str) -> str:
    """تطبيع السنة الدراسية الهجرية بصيغة YYYY-YYYY (مثل 1447-1448)."""
    v = (value or "").strip()
    return v.replace("–", "-").replace("—", "-")


def _validate_academic_year_hijri(value: str) -> None:
    """يتحقق من الصيغة 1447-1448 وأن السنة الثانية = الأولى + 1."""
    import re

    v = _normalize_academic_year_hijri(value)
    if not re.fullmatch(r"\d{4}-\d{4}", v):
        raise ValidationError("صيغة السنة الدراسية يجب أن تكون مثل 1447-1448")
    start, end = v.split("-", 1)
    try:
        s, e = int(start), int(end)
    except Exception as exc:
        raise ValidationError("صيغة السنة الدراسية غير صحيحة") from exc
    if e != s + 1:
        raise ValidationError("السنة الدراسية يجب أن تكون مثل 1447-1448 (فرق سنة واحدة)")


def _safe_unique_filename(filename: str, *, fallback: str = "file") -> str:
    base = os.path.basename(filename or fallback)
    base = get_valid_filename(base) or fallback
    stem, ext = os.path.splitext(base)
    stem = (stem or fallback)[:80].strip("._-") or fallback
    ext = (ext or "").lower()[:12]
    return f"{stem}_{secrets.token_hex(8)}{ext}"


def _achievement_pdf_upload_to(instance: "TeacherAchievementFile", filename: str) -> str:
    year = _normalize_academic_year_hijri(getattr(instance, "academic_year", "")) or "unknown"
    return f"achievements/pdfs/{year}/teacher_{instance.teacher_id}_{secrets.token_hex(8)}.pdf"


def _achievement_evidence_upload_to(instance: "AchievementEvidenceImage", filename: str) -> str:
    try:
        year = _normalize_academic_year_hijri(instance.section.file.academic_year)
    except Exception:
        year = "unknown"
    safe_name = _safe_unique_filename(filename, fallback="evidence")
    return f"achievements/evidence/{year}/section_{instance.section.code}/teacher_{instance.section.file.teacher_id}/{safe_name}"


def _leadership_evidence_upload_to(instance, filename: str) -> str:
    try:
        portfolio = instance.section.portfolio
        year = _normalize_academic_year_hijri(portfolio.academic_year) or "unknown"
        school_id = portfolio.school_id or "school"
        section_code = instance.section.code
    except Exception:
        year, school_id, section_code = "unknown", "school", "section"
    safe_name = _safe_unique_filename(filename, fallback="evidence")
    return f"leadership/evidence/{year}/school_{school_id}/section_{section_code}/{safe_name}"


def _payment_receipt_upload_to(instance: "Payment", filename: str) -> str:
    """مسار رفع صورة إيصال الدفع"""
    return f"payments/receipts/{_safe_unique_filename(filename, fallback='receipt')}"


def _report_image_upload_to(instance: "Report", filename: str) -> str:
    """مسار رفع صور التقرير"""
    base = _safe_unique_filename(filename, fallback="image")
    try:
        teacher_id = getattr(instance, "teacher_id", None) or "unknown"
    except Exception:
        teacher_id = "unknown"
    return f"reports/teacher_{teacher_id}/{base}"


def _ticket_attachment_upload_to(instance: "Ticket", filename: str) -> str:
    """مسار رفع مرفقات التذاكر"""
    return f"tickets/attachments/{_safe_unique_filename(filename, fallback='attachment')}"


def _notification_attachment_upload_to(instance: "Notification", filename: str) -> str:
    """مسار رفع مرفقات الإشعارات/التعاميم"""
    return f"notifications/attachments/{_safe_unique_filename(filename, fallback='attachment')}"


# NOTE: kept for historical migrations that referenced it.
def _school_logo_upload_to(instance: "School", filename: str) -> str:
    """مسار رفع شعار المدرسة (legacy)."""
    return f"schools/logos/{_safe_unique_filename(filename, fallback='logo')}"

def _ticket_image_upload_to(instance: "TicketImage", filename: str) -> str:
    """مسار رفع صور التذاكر"""
    return f"tickets/images/{_safe_unique_filename(filename, fallback='image')}"


# =========================
# المدرسة (Tenant)


# Make star imports include migration-facing helpers whose names start with "_".
__all__ = [name for name in globals() if not name.startswith("__")]
