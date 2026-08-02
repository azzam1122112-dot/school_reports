# reports/pdf_report.py
# -*- coding: utf-8 -*-
"""توليد PDF لتقرير واحد بإعادة استخدام قالب الطباعة الرسمي (report_print.html).

يُستخدم في تصدير/أرشفة بيانات المدرسة لإدراج كل تقرير كملف PDF مُنسّق
(ترويسة رسمية + جدول بيانات + الوصف + الصور + التواقيع)، بدل الاكتفاء بالصور.
"""
from __future__ import annotations

import base64
import logging
import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Tuple

from django.conf import settings
from django.contrib.staticfiles import finders
from django.template.loader import render_to_string
from django.templatetags.static import static

from .hijri_utils import hijri_date
from .models import SchoolMembership
from .utils import _resolve_department_for_category, _build_head_decision


logger = logging.getLogger(__name__)


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

    gender = (getattr(school, "gender", "") or "").strip().lower()
    girls_value = "girls"
    try:
        girls_value = str(getattr(getattr(type(school), "Gender", None), "GIRLS", "girls")).strip().lower()
    except Exception:
        pass
    is_girls_school = bool(school is not None and gender == girls_value)

    context = {
        "r": report,
        "head_decision": _build_head_decision(dept),
        "SCHOOL_PRINCIPAL": _school_principal_name(school),
        "SCHOOL_NAME": getattr(school, "name", "") if school else getattr(settings, "SCHOOL_NAME", "منصة التقارير المدرسية"),
        "SCHOOL_STAGE": school_stage,
        "SCHOOL_LOGO_URL": "",
        "MOE_LOGO_URL": _moe_logo_url(),
        "executor_label": "المنفّذة" if is_girls_school else "المنفّذ",
        "SCHOOL_MANAGER_LABEL": "مديرة المدرسة" if is_girls_school else "مدير المدرسة",
        "SCHOOL_HEAD_OF_DEPARTMENT_LABEL": "رئيسة القسم" if is_girls_school else "رئيس القسم",
        "EVIDENCE_COUNT": sum(
            bool(getattr(getattr(report, f"image{index}", None), "name", ""))
            for index in range(1, 5)
        ),
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
        "/usr/share/fonts/opentype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Regular.ttf",
        "/usr/share/fonts/opentype/noto/NotoKufiArabic-Regular.ttf",
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/tahoma.ttf",
        "/usr/share/fonts/opentype/noto/NotoNaskhArabic-Regular.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    raise OSError("No Arabic-capable fallback PDF font is installed.")


def _fallback_bold_font_path() -> str:
    configured = (getattr(settings, "PDF_FALLBACK_BOLD_FONT_PATH", "") or "").strip()
    candidates = [
        configured,
        "/usr/share/fonts/opentype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/truetype/noto/NotoSansArabic-Bold.ttf",
        "/usr/share/fonts/opentype/noto/NotoKufiArabic-Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
        "C:/Windows/Fonts/tahomabd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return candidate
    return _fallback_font_path()


def _fallback_logo_path() -> str:
    configured = (getattr(settings, "MOE_LOGO_STATIC", "") or "").strip()
    for static_path in (configured, "img/UntiTtled-1.png"):
        if not static_path:
            continue
        try:
            resolved = finders.find(static_path)
        except Exception:
            resolved = None
        if isinstance(resolved, (list, tuple)):
            resolved = resolved[0] if resolved else None
        if resolved and Path(resolved).is_file():
            return str(resolved)
    return ""


def _fallback_image_bytes(field) -> bytes:
    if not getattr(field, "name", ""):
        return b""
    field.open("rb")
    try:
        return field.read()
    finally:
        field.close()


def _generate_report_pdf_fallback(report, *, context: dict | None = None) -> bytes:
    """Generate the same official report experience without native HTML libraries."""
    import arabic_reshaper
    from bidi.algorithm import get_display
    from PIL import Image, ImageOps
    from reportlab.lib.colors import HexColor, white
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.utils import ImageReader
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    from reportlab.pdfgen import canvas

    context = context or build_report_print_context(report)
    regular_font = "TawtheeqReportArabic"
    bold_font = "TawtheeqReportArabicBold"
    if regular_font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(regular_font, _fallback_font_path()))
    if bold_font not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(TTFont(bold_font, _fallback_bold_font_path()))

    def rtl(value) -> str:
        text = str(value or "-")
        return get_display(arabic_reshaper.reshape(text), base_dir="R")

    def wrap(value, max_width: float, font_size: float, font_name: str = regular_font) -> list[str]:
        logical_lines: list[str] = []
        for paragraph in str(value or "-").splitlines() or ["-"]:
            words = paragraph.split()
            if not words:
                logical_lines.append("")
                continue
            current = words[0]
            for word in words[1:]:
                candidate = f"{current} {word}"
                if pdfmetrics.stringWidth(rtl(candidate), font_name, font_size) <= max_width:
                    current = candidate
                else:
                    logical_lines.append(current)
                    current = word
            logical_lines.append(current)
        return logical_lines

    green = HexColor("#006C35")
    green_dark = HexColor("#073D2B")
    green_soft = HexColor("#EDF6F1")
    gold = HexColor("#B9975B")
    gold_soft = HexColor("#F7F1E5")
    ink = HexColor("#17251F")
    muted = HexColor("#64736C")
    line_color = HexColor("#D6E0DA")

    output = BytesIO()
    page_width, page_height = A4
    pdf = canvas.Canvas(output, pagesize=A4, pageCompression=1, pdfVersion=(1, 4))
    title = getattr(report, "title", "") or "تقرير مدرسي"
    pdf.setTitle(title)
    pdf.setAuthor("منصة توثيق التقارير المدرسية")
    pdf.setSubject("تقرير مدرسي رسمي")

    margin = 42
    bottom_margin = 48
    content_width = page_width - (margin * 2)
    y = page_height - margin
    page_number = 1

    school = getattr(report, "school", None)
    school_name = getattr(school, "name", "") or getattr(
        settings, "SCHOOL_NAME", "المدرسة"
    )

    def draw_right(value, right_x, baseline_y, size=11, *, bold=False, color=ink):
        rendered = rtl(value)
        font_name = bold_font if bold else regular_font
        pdf.setFont(font_name, size)
        pdf.setFillColor(color)
        width = pdfmetrics.stringWidth(rendered, font_name, size)
        pdf.drawString(right_x - width, baseline_y, rendered)

    def draw_center(value, center_x, baseline_y, size=11, *, bold=False, color=ink):
        rendered = rtl(value)
        font_name = bold_font if bold else regular_font
        pdf.setFont(font_name, size)
        pdf.setFillColor(color)
        width = pdfmetrics.stringWidth(rendered, font_name, size)
        pdf.drawString(center_x - (width / 2), baseline_y, rendered)

    def draw_page_footer():
        pdf.saveState()
        pdf.setStrokeColor(line_color)
        pdf.setLineWidth(0.6)
        pdf.line(margin, 32, page_width - margin, 32)
        draw_center(
            f"منصة توثيق التقارير المدرسية  |  صفحة {page_number}",
            page_width / 2,
            20,
            7.3,
            color=muted,
        )
        pdf.restoreState()

    document_date = hijri_date(getattr(report, "report_date", None))
    document_day = getattr(report, "day_name", "") or ""
    document_ref = f"REP-{getattr(report, 'id', 'x')}"
    logo_path = _fallback_logo_path()

    def page_header():
        nonlocal y
        band_y = page_height - 42
        pdf.setFillColor(green)
        pdf.rect(margin, band_y, content_width * 0.72, 5, stroke=0, fill=1)
        pdf.setFillColor(gold)
        pdf.rect(margin + content_width * 0.72, band_y, content_width * 0.28, 5, stroke=0, fill=1)

        draw_right("المملكة العربية السعودية", page_width - margin, page_height - 59, 9.5, bold=True, color=green_dark)
        draw_right("وزارة التعليم", page_width - margin, page_height - 74, 9)
        draw_right(school_name, page_width - margin, page_height - 89, 9, bold=True)
        stage = context.get("SCHOOL_STAGE") or ""
        if stage:
            draw_right(stage, page_width - margin, page_height - 103, 7.8, color=muted)

        if logo_path:
            try:
                pdf.drawImage(
                    logo_path,
                    (page_width - 92) / 2,
                    page_height - 103,
                    width=92,
                    height=55,
                    preserveAspectRatio=True,
                    anchor="c",
                    mask="auto",
                )
            except Exception:
                pass

        left_right = margin + 142
        draw_right(f"التاريخ: {document_date} هـ", left_right, page_height - 61, 8.6, bold=True)
        if document_day:
            draw_right(f"اليوم: {document_day}", left_right, page_height - 76, 8.3)
        draw_right(f"مرجع الوثيقة: {document_ref}", left_right, page_height - 91, 8.3)

        pdf.setStrokeColor(line_color)
        pdf.setLineWidth(0.7)
        pdf.line(margin, page_height - 112, page_width - margin, page_height - 112)
        y = page_height - 128

    def new_page():
        nonlocal page_number
        draw_page_footer()
        pdf.showPage()
        page_number += 1
        page_header()

    def ensure_space(required):
        if y - required < bottom_margin:
            new_page()

    page_header()

    title_lines = wrap(title, content_width - 34, 16, bold_font)
    identity_height = 47 + (len(title_lines) * 21)
    ensure_space(identity_height + 10)
    pdf.setFillColor(HexColor("#FBFDFC"))
    pdf.setStrokeColor(line_color)
    pdf.roundRect(margin, y - identity_height, content_width, identity_height, 7, stroke=1, fill=1)
    pdf.setFillColor(green)
    pdf.rect(page_width - margin - 5, y - identity_height, 5, identity_height, stroke=0, fill=1)
    draw_right("وثيقة تقرير مدرسي", page_width - margin - 16, y - 18, 8.2, bold=True, color=gold)
    title_y = y - 42
    for title_line in title_lines:
        draw_right(title_line, page_width - margin - 16, title_y, 16, bold=True, color=green_dark)
        title_y -= 21
    academic_year = getattr(report, "academic_year", "") or ""
    tags = f"رقم التقرير: {getattr(report, 'id', '-')}    |    تاريخ التنفيذ: {document_date} هـ"
    if academic_year:
        tags += f"    |    العام الدراسي: {academic_year} هـ"
    draw_right(tags, page_width - margin - 16, y - identity_height + 12, 7.8, color=muted)
    y -= identity_height + 12

    category = getattr(getattr(report, "category", None), "name", "") or "عام"
    executor = (
        getattr(report, "teacher_display_name", "")
        or getattr(report, "teacher_name", "")
        or getattr(getattr(report, "teacher", None), "name", "")
        or "-"
    )
    beneficiaries = getattr(report, "beneficiaries_count", None)
    beneficiaries = beneficiaries if beneficiaries is not None else "-"
    metadata_rows = [
        (("التصنيف", category), ("تاريخ التنفيذ", f"{document_date} هـ")),
        ((context.get("executor_label") or "المنفّذ", executor), ("عدد المستفيدين", beneficiaries)),
    ]
    row_height = 27
    half_width = content_width / 2
    label_width = 72
    pdf.setStrokeColor(line_color)
    for right_pair, left_pair in metadata_rows:
        ensure_space(row_height)
        for pair, left in ((right_pair, margin + half_width), (left_pair, margin)):
            label, value = pair
            pdf.setFillColor(white)
            pdf.rect(left, y - row_height, half_width, row_height, stroke=1, fill=1)
            pdf.setFillColor(green_soft)
            pdf.rect(left + half_width - label_width, y - row_height, label_width, row_height, stroke=1, fill=1)
            draw_right(label, left + half_width - 7, y - 18, 8.2, bold=True, color=green_dark)
            draw_right(value, left + half_width - label_width - 7, y - 18, 8.5, bold=True)
        y -= row_height
    y -= 14

    def draw_section_heading(number, heading, *, continued=False):
        nonlocal y
        ensure_space(30)
        pdf.setFillColor(green)
        pdf.circle(page_width - margin - 11, y - 8, 10, stroke=0, fill=1)
        draw_center(number, page_width - margin - 11, y - 11, 6.8, bold=True, color=white)
        suffix = " - تابع" if continued else ""
        draw_right(f"{heading}{suffix}", page_width - margin - 29, y - 12, 10.5, bold=True, color=green_dark)
        y -= 29

    description_lines = wrap(getattr(report, "idea", "") or "-", content_width - 24, 10.2)
    draw_section_heading("01", "الوصف التنفيذي")
    first_description_chunk = True
    while description_lines:
        available_lines = max(0, int((y - bottom_margin - 26) // 16.5))
        if available_lines < 2:
            new_page()
            draw_section_heading("01", "الوصف التنفيذي", continued=not first_description_chunk)
            available_lines = max(2, int((y - bottom_margin - 26) // 16.5))
        chunk = description_lines[:available_lines]
        description_lines = description_lines[available_lines:]
        box_height = max(54, 20 + (len(chunk) * 16.5))
        pdf.setFillColor(white)
        pdf.setStrokeColor(line_color)
        pdf.roundRect(margin, y - box_height, content_width, box_height, 6, stroke=1, fill=1)
        line_y = y - 20
        for logical_line in chunk:
            draw_right(logical_line, page_width - margin - 12, line_y, 10.2)
            line_y -= 16.5
        y -= box_height + 13
        first_description_chunk = False
        if description_lines:
            new_page()
            draw_section_heading("01", "الوصف التنفيذي", continued=True)

    images = []
    for index in range(1, 5):
        field = getattr(report, f"image{index}", None)
        try:
            data = _fallback_image_bytes(field)
        except Exception:
            data = b""
        if data:
            images.append((index, data))

    def prepared_image_reader(data):
        original = BytesIO(data)
        source = Image.open(original)
        orientation = source.getexif().get(274, 1)
        if orientation in (None, 1):
            original.seek(0)
            return ImageReader(original)

        # Correct camera orientation only when needed. PNG keeps the decoded pixels
        # lossless instead of applying another lossy JPEG compression pass.
        source = ImageOps.exif_transpose(source)
        corrected = BytesIO()
        source.save(corrected, format="PNG", optimize=True)
        corrected.seek(0)
        return ImageReader(corrected)

    if images:
        gap = 10
        rows = (len(images) + 1) // 2
        box_height = 88 if rows >= 2 else 126
        row_height = box_height + 9
        signature_reserve = 105
        first_row_reserve = signature_reserve if rows == 1 else 0
        if y - (29 + row_height + first_row_reserve) < bottom_margin:
            new_page()
        draw_section_heading("02", "المرفقات والشواهد")
        for offset in range(0, len(images), 2):
            row = images[offset : offset + 2]
            if y - row_height - (signature_reserve if offset + 2 >= len(images) else 0) < bottom_margin:
                new_page()
                draw_section_heading("02", "المرفقات والشواهد", continued=True)
            if len(row) == 1:
                box_width = content_width * 0.64
                row_positions = [margin + ((content_width - box_width) / 2)]
            else:
                box_width = (content_width - gap) / 2
                row_positions = [page_width - margin - box_width, margin]
            for column, (index, data) in enumerate(row):
                left = row_positions[column]
                pdf.setFillColor(white)
                pdf.setStrokeColor(line_color)
                pdf.roundRect(left, y - box_height, box_width, box_height, 6, stroke=1, fill=1)
                try:
                    reader = prepared_image_reader(data)
                    img_width, img_height = reader.getSize()
                    scale = min(
                        (box_width - 16) / max(img_width, 1),
                        (box_height - 32) / max(img_height, 1),
                    )
                    draw_width = img_width * scale
                    draw_height = img_height * scale
                    pdf.drawImage(
                        reader,
                        left + ((box_width - draw_width) / 2),
                        y - box_height + 24 + ((box_height - 30 - draw_height) / 2),
                        width=draw_width,
                        height=draw_height,
                        preserveAspectRatio=True,
                        mask="auto",
                    )
                except Exception:
                    draw_center("تعذرت قراءة الصورة", left + (box_width / 2), y - 58, 9, color=muted)
                draw_right(f"مرفق ({index})", left + box_width - 8, y - box_height + 9, 7.5, bold=True, color=muted)
            y -= row_height
        y -= 5

    ensure_space(105)
    pdf.setStrokeColor(line_color)
    pdf.line(margin, y, page_width - margin, y)
    draw_right("الاعتمادات والتوقيعات", page_width - margin, y - 16, 10.5, bold=True, color=green_dark)
    draw_right("يعتمد التقرير بعد مراجعة محتواه وشواهده", margin + 205, y - 16, 7.2, color=muted)
    y -= 42

    principal = context.get("SCHOOL_PRINCIPAL") or "........................"
    roles = [(context.get("executor_label") or "المنفّذ", executor)]
    head_decision = context.get("head_decision") or {}
    if head_decision and not head_decision.get("no_render"):
        head_name = "........................"
        if head_decision.get("single"):
            head_name = head_decision.get("name") or head_name
        elif head_decision.get("multi_dept"):
            head_name = head_decision.get("dept_name") or head_name
        roles.append((context.get("SCHOOL_HEAD_OF_DEPARTMENT_LABEL") or "رئيس القسم", head_name))
    roles.append((context.get("SCHOOL_MANAGER_LABEL") or "مدير المدرسة", principal))

    column_width = content_width / len(roles)
    for index, (role_label, person_name) in enumerate(roles):
        center_x = page_width - margin - (column_width * index) - (column_width / 2)
        draw_center(role_label, center_x, y, 9.2, bold=True, color=green_dark)
        line_y = y - 44
        pdf.setStrokeColor(HexColor("#738079"))
        pdf.setLineWidth(0.7)
        pdf.line(center_x - (column_width * 0.34), line_y, center_x + (column_width * 0.34), line_y)
        draw_center(f"الاسم: {person_name}", center_x, line_y - 13, 7.7, color=ink)

    draw_page_footer()
    pdf.save()
    return output.getvalue()


def generate_report_pdf(*, request, report) -> Tuple[bytes, str]:
    """يولّد PDF لتقرير ويعيد (bytes, filename).

    - يعيد استخدام قالب الطباعة الرسمي لضمان تطابق الشكل مع طباعة المتصفح.
    - WeasyPrint يطبّق أنماط ``@media print`` فيخفي شريط الأدوات والتعليقات تلقائيًا.
    """
    context = build_report_print_context(report)
    html = render_to_string("reports/report_print.html", context)

    base_url = None
    try:
        base_url = request.build_absolute_uri("/")
    except Exception:
        base_url = None

    try:
        pdf_bytes = _generate_report_pdf_weasy(html=html, base_url=base_url)
    except Exception as exc:
        logger.warning(
            "WeasyPrint report rendering failed; using official ReportLab fallback report_id=%s error=%s",
            getattr(report, "id", None),
            type(exc).__name__,
        )
        pdf_bytes = _generate_report_pdf_fallback(report, context=context)
    filename = f"report_{getattr(report, 'id', 'x')}.pdf"
    return pdf_bytes, filename
