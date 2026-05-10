# reports/models.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from datetime import timedelta
from typing import Optional
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
from django.utils.text import slugify
from django.utils import timezone

# تخزين المرفقات (R2 أو محلي)
from ..storage import PublicRawMediaStorage
from ..validators import validate_attachment_file, validate_circular_attachment_file, validate_image_file, validate_pdf_file

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
    except Exception:
        raise ValidationError("صيغة السنة الدراسية غير صحيحة")
    if e != s + 1:
        raise ValidationError("السنة الدراسية يجب أن تكون مثل 1447-1448 (فرق سنة واحدة)")


def _achievement_pdf_upload_to(instance: "TeacherAchievementFile", filename: str) -> str:
    year = _normalize_academic_year_hijri(getattr(instance, "academic_year", "")) or "unknown"
    return f"achievements/pdfs/{year}/teacher_{instance.teacher_id}.pdf"


def _achievement_evidence_upload_to(instance: "AchievementEvidenceImage", filename: str) -> str:
    try:
        year = _normalize_academic_year_hijri(instance.section.file.academic_year)
    except Exception:
        year = "unknown"
    return f"achievements/evidence/{year}/section_{instance.section.code}/teacher_{instance.section.file.teacher_id}/{filename}"


def _payment_receipt_upload_to(instance: "Payment", filename: str) -> str:
    """مسار رفع صورة إيصال الدفع"""
    return f"payments/receipts/{filename}"


def _report_image_upload_to(instance: "Report", filename: str) -> str:
    """مسار رفع صور التقرير"""
    import os
    import uuid

    base = os.path.basename(filename or "image")
    uid = uuid.uuid4().hex
    try:
        teacher_id = getattr(instance, "teacher_id", None) or "unknown"
    except Exception:
        teacher_id = "unknown"
    return f"reports/teacher_{teacher_id}/{uid}_{base}"


def _ticket_attachment_upload_to(instance: "Ticket", filename: str) -> str:
    """مسار رفع مرفقات التذاكر"""
    return f"tickets/attachments/{filename}"


def _notification_attachment_upload_to(instance: "Notification", filename: str) -> str:
    """مسار رفع مرفقات الإشعارات/التعاميم"""
    return f"notifications/attachments/{filename}"


# NOTE: kept for historical migrations that referenced it.
def _school_logo_upload_to(instance: "School", filename: str) -> str:
    """مسار رفع شعار المدرسة (legacy)."""
    return f"schools/logos/{filename}"

def _ticket_image_upload_to(instance: "TicketImage", filename: str) -> str:
    """مسار رفع صور التذاكر"""
    return f"tickets/images/{filename}"


# =========================
# المدرسة (Tenant)


# Make star imports include migration-facing helpers whose names start with "_".
__all__ = [name for name in globals() if not name.startswith("__")]
