# reports/pdf_achievement.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple

from django.template.loader import render_to_string
from django.utils import timezone

from .models import TeacherAchievementFile, AchievementSection


def generate_achievement_pdf(*, request, ach_file: TeacherAchievementFile) -> Tuple[bytes, str]:
    """Generate an achievement file PDF.

    Returns: (pdf_bytes, suggested_filename)

    Notes:
    - Uses WeasyPrint (system deps installed in Dockerfile).
    - PDF is generated on-demand; caller decides whether to persist it.
    """

    sections = (
        AchievementSection.objects.filter(file=ach_file)
        .prefetch_related("evidence_images")
        .order_by("code", "id")
    )

    school = ach_file.school
    primary = (getattr(school, "print_primary_color", None) or "").strip() or "#2563eb"
    gender = (getattr(school, "gender", "") or "").strip().lower()
    gender_label = "بنين" if gender == "boys" else ("بنات" if gender == "girls" else "")

    ctx = {
        "file": ach_file,
        "school": school,
        "sections": sections,
        "theme": {"brand": primary},
        "now": timezone.localtime(timezone.now()),
    }

    html = render_to_string("reports/pdf/achievement_file.html", ctx)

    # WeasyPrint import kept inside to avoid import-time failures in dev environments.
    from weasyprint import HTML

    base_url = None
    try:
        base_url = request.build_absolute_uri("/")
    except Exception:
        base_url = None

    pdf_bytes = HTML(string=html, base_url=base_url).write_pdf()

    safe_teacher = (ach_file.teacher_name or "teacher").replace("/", "-")
    year = (ach_file.academic_year or "").strip() or "year"
    filename = f"achievement_{safe_teacher}_{year}.pdf"
    return pdf_bytes, filename
