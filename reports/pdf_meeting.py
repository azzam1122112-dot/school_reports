from __future__ import annotations

from io import BytesIO
from typing import Tuple

from django.template.loader import render_to_string
from django.utils import timezone

from core.observability import report_degraded as _degraded

from .gender_labels import school_gender_template_context
from .pdf_report import (
    _fallback_bold_font_path,
    _fallback_font_path,
    _fallback_logo_path,
    _moe_logo_url,
    _school_principal_name,
)
from .pdf_render import prefer_reportlab_for_official_arabic, render_html_pdf
from .services_meetings import decision_followup_rows


def build_meeting_print_context(meeting, *, active_school=None, for_pdf: bool = False) -> dict:
    school = getattr(meeting, "school", None) or active_school
    return {
        "active_school": active_school or school,
        "meeting": meeting,
        "agenda": list(meeting.agenda_items.all()),
        "attendees": list(meeting.attendees.select_related("person")),
        "attendance": meeting.attendance_summary,
        "minutes": getattr(meeting, "minutes", None),
        "decisions": decision_followup_rows(meeting),
        "now": timezone.localtime(timezone.now()),
        "MOE_LOGO_URL": _moe_logo_url(),
        "SCHOOL_NAME": getattr(school, "name", "") or "المدرسة",
        "SCHOOL_PRINCIPAL": _school_principal_name(school),
        "for_pdf": for_pdf,
        **school_gender_template_context(school),
    }


def _generate_meeting_pdf_weasy(*, html: str, base_url: str | None) -> bytes:
    return render_html_pdf(html=html, base_url=base_url)


def _generate_meeting_pdf_fallback(meeting, *, context: dict | None = None) -> bytes:
    """نسخة احتياطية كاملة عند غياب WeasyPrint."""
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.lib.colors import HexColor
    from reportlab.lib.pagesizes import A4
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas
    from reportlab.lib.utils import ImageReader

    context = context or build_meeting_print_context(meeting, for_pdf=True)
    regular, bold = "MeetingArabic", "MeetingArabicBold"
    if regular not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular, _fallback_font_path()))
    if bold not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(bold, _fallback_bold_font_path()))

    def rtl(value):
        return get_display(arabic_reshaper.reshape(str(value or "")), base_dir="R")

    output = BytesIO()
    page_width, page_height = A4
    pdf = canvas.Canvas(output, pagesize=A4, pageCompression=1)
    pdf.setTitle(f"محضر اجتماع MTG-{meeting.pk}")
    pdf.setAuthor("منصة توثيق")
    margin, bottom = 42, 42
    green, gold, ink, line = map(HexColor, ("#006c35", "#b9975b", "#17251f", "#d6e0da"))
    y = page_height - 42
    page_no = 0

    def right(text, x, baseline, size=10, font=regular, color=ink):
        rendered = rtl(text)
        pdf.setFont(font, size)
        pdf.setFillColor(color)
        pdf.drawString(x - pdfmetrics.stringWidth(rendered, font, size), baseline, rendered)

    def start_page():
        nonlocal y, page_no
        if page_no:
            pdf.showPage()
        page_no += 1
        pdf.setFillColor(green)
        pdf.rect(margin, page_height - 33, (page_width - 2 * margin) * .72, 5, fill=1, stroke=0)
        pdf.setFillColor(gold)
        pdf.rect(margin + (page_width - 2 * margin) * .72, page_height - 33, (page_width - 2 * margin) * .28, 5, fill=1, stroke=0)
        right("المملكة العربية السعودية - وزارة التعليم", page_width - margin, page_height - 55, 9, bold, green)
        right(context["SCHOOL_NAME"], page_width - margin, page_height - 72, 9, bold)
        logo_path = _fallback_logo_path()
        if logo_path:
            try:
                pdf.drawImage(
                    ImageReader(logo_path),
                    (page_width / 2) - 42,
                    page_height - 91,
                    width=84,
                    height=50,
                    preserveAspectRatio=True,
                    anchor="c",
                    mask="auto",
                )
            except Exception as exc:
                _degraded("meeting_pdf.draw_logo", error=type(exc).__name__)
        right(f"محضر اجتماع رقم MTG-{meeting.pk}", page_width - margin, page_height - 102, 15, bold, green)
        pdf.setStrokeColor(line)
        pdf.line(margin, 34, page_width - margin, 34)
        right(f"منصة توثيق | صفحة {page_no}", page_width - margin, 20, 7, regular, HexColor("#64736c"))
        y = page_height - 128

    def wrapped(text, size=9.5, font=regular, max_width=None):
        max_width = max_width or page_width - (2 * margin) - 18
        words = str(text or "—").split()
        lines, current = [], ""
        for word in words:
            candidate = f"{current} {word}".strip()
            if pdfmetrics.stringWidth(rtl(candidate), font, size) <= max_width:
                current = candidate
            else:
                if current:
                    lines.append(current)
                current = word
        if current:
            lines.append(current)
        return lines or ["—"]

    def section(title, body):
        nonlocal y
        lines = wrapped(body)
        continuation = False
        while lines:
            # Reserve the section title, box padding, footer, and at least two
            # lines. Long sections are continued cleanly on a fresh page.
            if y - bottom < 76:
                start_page()
            available_lines = max(2, int((y - bottom - 49) // 16))
            page_lines, lines = lines[:available_lines], lines[available_lines:]
            heading = f"{title} — تابع" if continuation else title
            right(heading, page_width - margin, y, 10.5, bold, green)
            y -= 18
            box_height = len(page_lines) * 16 + 14
            pdf.setFillColor(HexColor("#fbfdfc"))
            pdf.setStrokeColor(line)
            pdf.roundRect(
                margin,
                y - len(page_lines) * 16 - 12,
                page_width - 2 * margin,
                box_height,
                5,
                fill=1,
                stroke=1,
            )
            for row in page_lines:
                right(row, page_width - margin - 9, y - 13, 9.5)
                y -= 16
            y -= 22
            continuation = True
            if lines:
                start_page()

    start_page()
    section("بيانات الاجتماع", f"الموضوع: {meeting.title} | المكان: {meeting.location or '—'} | المنظم: {meeting.organizer_name or getattr(meeting.organizer, 'name', '')}")
    if meeting.purpose:
        section("الغرض من الاجتماع", meeting.purpose)
    if context["agenda"]:
        section("جدول الأعمال", "\n".join(f"{i}. {item.title}" for i, item in enumerate(context["agenda"], 1)))
    minutes = context.get("minutes")
    if minutes and minutes.format_mode == "structured":
        for title, value in (
            ("مجريات الاجتماع", minutes.proceedings),
            ("أبرز النقاشات", minutes.discussions),
            ("ملخص القرارات", minutes.decisions_summary),
            ("التوصيات", minutes.recommendations),
            ("التكليفات", minutes.assignments_summary),
        ):
            if value:
                section(title, value)
    else:
        section("نص المحضر", getattr(minutes, "body", "") or "—")
    decisions = context.get("decisions") or []
    if decisions:
        section("القرارات والتوصيات", "\n".join(f"{i}. {row['decision'].title}" for i, row in enumerate(decisions, 1)))
    attendees = context.get("attendees") or []
    section("الحضور", " | ".join((att.person_name or getattr(att.person, "name", "")) for att in attendees) or "—")
    section("الاعتمادات والتوقيعات", f"كاتب المحضر: {getattr(getattr(minutes, 'recorder', None), 'name', '') or '........................'} | مدير المدرسة: {context['SCHOOL_PRINCIPAL'] or '........................'}")
    pdf.save()
    return output.getvalue()


def generate_meeting_pdf(*, request, meeting) -> Tuple[bytes, str]:
    context = build_meeting_print_context(meeting, active_school=getattr(meeting, "school", None), for_pdf=True)
    html = render_to_string("reports/meeting_print.html", context)
    base_url = request.build_absolute_uri("/") if request is not None else None
    if prefer_reportlab_for_official_arabic():
        pdf_bytes = _generate_meeting_pdf_fallback(meeting, context=context)
    else:
        try:
            pdf_bytes = _generate_meeting_pdf_weasy(html=html, base_url=base_url)
        except Exception:
            pdf_bytes = _generate_meeting_pdf_fallback(meeting, context=context)
    return pdf_bytes, f"meeting_{meeting.pk}.pdf"
