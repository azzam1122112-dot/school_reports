# reports/pdf_achievement.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from dataclasses import dataclass
from typing import Tuple
import base64

from django.template.loader import render_to_string
from django.utils import timezone
from django.contrib.staticfiles import finders

from .models import TeacherAchievementFile, AchievementEvidenceReport, AchievementSection


def _static_png_as_data_uri(path: str) -> str | None:
    try:
        fpath = finders.find(path)
        if not fpath:
            return None
        with open(fpath, "rb") as f:
            b64 = base64.b64encode(f.read()).decode("ascii")
        return f"data:image/png;base64,{b64}"
    except Exception:
        return None


def generate_achievement_pdf(*, request, ach_file: TeacherAchievementFile) -> Tuple[bytes, str]:
    """Generate an achievement file PDF.

    Returns: (pdf_bytes, suggested_filename)

    Notes:
    - Uses WeasyPrint (system deps installed in Dockerfile).
    - PDF is generated on-demand; caller decides whether to persist it.
    """

    from django.db.models import Prefetch

    ev_reports_qs = AchievementEvidenceReport.objects.select_related(
        "report",
        "report__category",
    ).order_by("id")
    sections = (
        AchievementSection.objects.filter(file=ach_file)
        .prefetch_related("evidence_images", Prefetch("evidence_reports", queryset=ev_reports_qs))
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
        "has_evidence_reports": AchievementEvidenceReport.objects.filter(section__file=ach_file).exists(),
        "theme": {"brand": primary},
        "now": timezone.localtime(timezone.now()),
        "ministry_logo_src": _static_png_as_data_uri("img/UntiTtled-1.png"),
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
