from __future__ import annotations

from django.conf import settings
from django.template.loader import render_to_string

from .models import LeadershipEvidenceImage, LeadershipPortfolioSection


def build_leadership_print_context(portfolio) -> dict:
    sections = portfolio.sections.prefetch_related("evidence_images").order_by("code", "id")
    completed = sections.filter(is_completed=True).count()
    total = len(LeadershipPortfolioSection.Code.choices)
    return {
        "portfolio": portfolio,
        "sections": sections,
        "current_school": portfolio.school,
        "completed_sections": completed,
        "total_sections": total,
        "completion_percent": int((completed / total) * 100),
        "evidence_count": LeadershipEvidenceImage.objects.filter(
            section__portfolio=portfolio
        ).count(),
        "manager_label": (
            "مديرة المدرسة" if portfolio.school.gender == "girls" else "مدير المدرسة"
        ),
    }


def generate_leadership_portfolio_pdf(portfolio, *, request=None) -> bytes:
    html = render_to_string(
        "reports/pdf/leadership_portfolio.html",
        build_leadership_print_context(portfolio),
        request=request,
    )
    from weasyprint import HTML

    base_url = (
        request.build_absolute_uri("/")
        if request is not None
        else str(getattr(settings, "BASE_DIR", "") or "")
    )
    return HTML(string=html, base_url=base_url).write_pdf()
