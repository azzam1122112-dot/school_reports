from __future__ import annotations

from django.conf import settings
from django.template.loader import render_to_string

from .gender_labels import school_gender_template_context
from .models import (
    LeadershipEvidenceImage,
    LeadershipEvidenceReport,
    LeadershipPortfolioSection,
)


def build_leadership_print_context(portfolio) -> dict:
    sections = portfolio.sections.prefetch_related(
        "evidence_images",
        "evidence_reports__report__category",
    ).order_by("code", "id")
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
        "report_evidence_count": LeadershipEvidenceReport.objects.filter(
            section__portfolio=portfolio
        ).count(),
        **school_gender_template_context(portfolio.school),
        "manager_label": school_gender_template_context(portfolio.school)["SCHOOL_MANAGER_LABEL"],
    }


def generate_leadership_portfolio_pdf(portfolio, *, request=None, base_url=None) -> bytes:
    """يولّد ملف الأداء القيادي PDF.

    ``base_url`` بديلٌ صريح عن ``request`` كي تعمل الدالة في عامل الوسائط حيث
    لا طلبَ أصلاً. والقالب لا يعتمد على معالجات السياق: مسمّيات
    ``SCHOOL_*_LABEL`` تأتي من ``build_leadership_print_context`` نفسه — فالناتج
    واحدٌ سواء وُجد الطلب أم لا.
    """
    html = render_to_string(
        "reports/pdf/leadership_portfolio.html",
        build_leadership_print_context(portfolio),
        request=request,
    )
    from weasyprint import HTML

    if base_url is None:
        base_url = (
            request.build_absolute_uri("/")
            if request is not None
            else str(getattr(settings, "BASE_DIR", "") or "")
        )
    return HTML(string=html, base_url=base_url).write_pdf()
