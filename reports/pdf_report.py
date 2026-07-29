# reports/pdf_report.py
# -*- coding: utf-8 -*-
"""توليد PDF لتقرير واحد بإعادة استخدام قالب الطباعة الرسمي (report_print.html).

يُستخدم في تصدير/أرشفة بيانات المدرسة لإدراج كل تقرير كملف PDF مُنسّق
(ترويسة رسمية + جدول بيانات + الوصف + الصور + التواقيع)، بدل الاكتفاء بالصور.
"""
from __future__ import annotations

import base64
import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Tuple

from django.conf import settings
from django.template.loader import render_to_string
from django.templatetags.static import static

from .models import SchoolMembership
from .utils import _resolve_department_for_category, _build_head_decision


def _file_data_uri(field) -> str:
    """Return a self-contained URL so private report images render in the PDF."""
    if not getattr(field, "name", ""):
        return ""
    try:
        field.open("rb")
        try:
            data = field.read()
        finally:
            field.close()
    except Exception:
        return ""
    content_type = mimetypes.guess_type(field.name)[0] or "image/jpeg"
    return f"data:{content_type};base64,{base64.b64encode(data).decode('ascii')}"


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

    context = {
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
    for index in range(1, 5):
        context[f"PDF_IMAGE{index}_URL"] = _file_data_uri(
            getattr(report, f"image{index}", None)
        )
    return context


def _generate_report_pdf_weasy(*, html: str, base_url: str | None) -> bytes:
    from weasyprint import HTML

    return HTML(string=html, base_url=base_url).write_pdf()


def _fallback_font_path() -> str:
    configured = (getattr(settings, "PDF_FALLBACK_FONT_PATH", "") or "").strip()
    candidates = [
        configured,
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise OSError("No Arabic-capable fallback PDF font is installed.")


def _fallback_image_bytes(field) -> bytes:
    if not getattr(field, "name", ""):
        return b""
    field.open("rb")
    try:
        return field.read()
    finally:
        field.close()


def _generate_report_pdf_fallback(report) -> bytes:
    """Pure-Python PDF fallback used when WeasyPrint native libraries are absent."""
    import arabic_reshaper
    from bidi.algorithm import get_display
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    regular_font = "TawtheeqArchiveArabic"
    if regular_font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular_font, _fallback_font_path()))

    def rtl(value) -> str:
        text = str(value or "-")
        return get_display(arabic_reshaper.reshape(text), base_dir="R")

    def wrap(value, max_width: float, font_size: float) -> list[str]:
        logical_lines: list[str] = []
        for paragraph in str(value or "-").splitlines() or ["-"]:
            words = paragraph.split()
            if not words:
                logical_lines.append("")
                continue
            current = words[0]
            for word in words[1:]:
                candidate = f"{current} {word}"
                if pdfmetrics.stringWidth(rtl(candidate), regular_font, font_size) <= max_width:
                    current = candidate
                else:
                    logical_lines.append(current)
                    current = word
            logical_lines.append(current)
        return logical_lines

    output = BytesIO()
    page_width, page_height = A4
    pdf = canvas.Canvas(output, pagesize=A4, pageCompression=1)
    margin = 42
    content_width = page_width - (margin * 2)
    y = page_height - margin

    school = getattr(report, "school", None)
    school_name = getattr(school, "name", "") or getattr(
        settings, "SCHOOL_NAME", "المدرسة"
    )

    def draw_right(value, right_x, baseline_y, size=11):
        rendered = rtl(value)
        pdf.setFont(regular_font, size)
        width = pdfmetrics.stringWidth(rendered, regular_font, size)
        pdf.drawString(right_x - width, baseline_y, rendered)

    def page_header():
        nonlocal y
        pdf.setStrokeColorRGB(0, 0.42, 0.21)
        pdf.setLineWidth(2)
        pdf.line(margin, page_height - 68, page_width - margin, page_height - 68)
        draw_right("المملكة العربية السعودية - وزارة التعليم", page_width - margin, page_height - 48, 10)
        draw_right(school_name, page_width - margin, page_height - 62, 10)
        pdf.setFont(regular_font, 15)
        heading = rtl("تقرير مدرسي")
        heading_width = pdfmetrics.stringWidth(heading, regular_font, 15)
        pdf.drawString((page_width - heading_width) / 2, page_height - 92, heading)
        y = page_height - 112

    def new_page():
        pdf.showPage()
        page_header()

    def ensure_space(required):
        if y - required < margin:
            new_page()

    page_header()

    metadata = [
        ("عنوان التقرير", getattr(report, "title", "") or "-"),
        ("رقم التقرير", getattr(report, "id", "") or "-"),
        ("تاريخ التنفيذ", getattr(report, "report_date", "") or "-"),
        (
            "التصنيف",
            getattr(getattr(report, "category", None), "name", "") or "عام",
        ),
        (
            "المنفذ",
            getattr(report, "teacher_display_name", "")
            or getattr(report, "teacher_name", "")
            or getattr(getattr(report, "teacher", None), "name", "")
            or "-",
        ),
        ("عدد المستفيدين", getattr(report, "beneficiaries_count", "") or "-"),
    ]
    pdf.setStrokeColorRGB(0.75, 0.75, 0.75)
    for label, value in metadata:
        ensure_space(25)
        pdf.rect(margin, y - 17, content_width, 23, stroke=1, fill=0)
        draw_right(f"{label}: {value}", page_width - margin - 8, y - 10, 10.5)
        y -= 23

    y -= 12
    ensure_space(45)
    draw_right("الوصف", page_width - margin, y, 13)
    y -= 20
    for line in wrap(getattr(report, "idea", "") or "-", content_width - 16, 11):
        ensure_space(18)
        draw_right(line, page_width - margin - 8, y, 11)
        y -= 18

    images = []
    for index in range(1, 5):
        field = getattr(report, f"image{index}", None)
        try:
            data = _fallback_image_bytes(field)
        except Exception:
            data = b""
        if data:
            images.append((index, data))

    if images:
        y -= 10
        ensure_space(35)
        draw_right("المرفقات والشواهد", page_width - margin, y, 13)
        y -= 20
        gap = 10
        box_width = (content_width - gap) / 2
        box_height = 170
        for offset in range(0, len(images), 2):
            ensure_space(box_height + 22)
            row = images[offset : offset + 2]
            for column, (index, data) in enumerate(row):
                left = page_width - margin - box_width if column == 0 else margin
                pdf.rect(left, y - box_height, box_width, box_height, stroke=1, fill=0)
                try:
                    reader = ImageReader(BytesIO(data))
                    img_width, img_height = reader.getSize()
                    scale = min(
                        (box_width - 12) / max(img_width, 1),
                        (box_height - 30) / max(img_height, 1),
                    )
                    draw_width = img_width * scale
                    draw_height = img_height * scale
                    pdf.drawImage(
                        reader,
                        left + ((box_width - draw_width) / 2),
                        y - box_height + 22 + ((box_height - 28 - draw_height) / 2),
                        width=draw_width,
                        height=draw_height,
                        preserveAspectRatio=True,
                        mask="auto",
                    )
                except Exception:
                    draw_right("تعذرت قراءة الصورة", left + box_width - 8, y - 45, 10)
                draw_right(f"مرفق ({index})", left + box_width - 8, y - box_height + 8, 9)
            y -= box_height + 12

    ensure_space(75)
    y -= 15
    principal = _school_principal_name(school) or "........................"
    executor = metadata[4][1]
    draw_right(f"المنفذ: {executor}", page_width - margin, y, 10.5)
    draw_right(f"مدير المدرسة: {principal}", page_width / 2, y, 10.5)
    y -= 35
    draw_right("التوقيع: ........................", page_width - margin, y, 10)
    draw_right("التوقيع: ........................", page_width / 2, y, 10)

    pdf.save()
    return output.getvalue()


def generate_report_pdf(*, request, report) -> Tuple[bytes, str]:
    """يولّد PDF لتقرير ويعيد (bytes, filename).

    - يعيد استخدام قالب الطباعة الرسمي لضمان تطابق الشكل مع طباعة المتصفح.
    - WeasyPrint يطبّق أنماط ``@media print`` فيخفي شريط الأدوات والتعليقات تلقائيًا.
    """
    html = render_to_string("reports/report_print.html", build_report_print_context(report))

    base_url = None
    try:
        base_url = request.build_absolute_uri("/")
    except Exception:
        base_url = None

    try:
        pdf_bytes = _generate_report_pdf_weasy(html=html, base_url=base_url)
    except Exception:
        pdf_bytes = _generate_report_pdf_fallback(report)
    filename = f"report_{getattr(report, 'id', 'x')}.pdf"
    return pdf_bytes, filename
