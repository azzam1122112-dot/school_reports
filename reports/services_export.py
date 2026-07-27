# reports/services_export.py
# -*- coding: utf-8 -*-
"""تصدير بيانات المدرسة الكاملة كملف Excel احترافي (.xlsx).

يشمل: ملخص عام + التقارير + ملفات الإنجاز + المعلمون + الأقسام.
يصدّر البيانات الوصفية (لا ملفات الصور) ليكون التنزيل سريعًا وخفيفًا،
ويصلح كنسخة احتياطية يملكها العميل.
"""
from __future__ import annotations

from io import BytesIO

from django.utils import timezone

from .models import (
    Department,
    DepartmentMembership,
    Notification,
    Report,
    SchoolMembership,
    TeacherAchievementFile,
    Ticket,
    TicketImage,
)

# ألوان الهوية
_BRAND_GREEN = "006C35"
_BRAND_BLUE = "0072BC"
_HEADER_FILL = "0F8F6B"
_ZEBRA = "F1F8F4"


def _counts(school) -> dict:
    return {
        "reports": Report.objects.filter(school=school).count(),
        "achievements": TeacherAchievementFile.objects.filter(school=school).count(),
        "tickets": Ticket.objects.filter(school=school).count(),
        "circulars": Notification.objects.filter(
            school=school, requires_signature=True
        ).count(),
        "notifications": Notification.objects.filter(
            school=school, requires_signature=False
        ).count(),
        "teachers": SchoolMembership.objects.filter(
            school=school, role_type=SchoolMembership.RoleType.TEACHER
        ).count(),
        "departments": Department.objects.filter(school=school).count(),
    }


def export_filename(school) -> str:
    code = (getattr(school, "code", "") or "school").strip() or "school"
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    return f"school-data-{code}-{stamp}.xlsx"


def build_school_export_workbook(school):
    """يبني ويُعيد Workbook لبيانات المدرسة الكاملة."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill, Border, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    wb.properties.creator = "توثيق - منصة تقارير المدارس"

    header_font = Font(name="Calibri", bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill("solid", fgColor=_HEADER_FILL)
    title_font = Font(name="Calibri", bold=True, color=_BRAND_GREEN, size=16)
    label_font = Font(name="Calibri", bold=True, color=_BRAND_BLUE, size=11)
    right = Alignment(horizontal="right", vertical="center", wrap_text=True, readingOrder=2)
    center = Alignment(horizontal="center", vertical="center")
    thin = Side(style="thin", color="D6E4DC")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    def _style_sheet_rtl(ws):
        ws.sheet_view.rightToLeft = True

    def _write_table(ws, headers, rows, *, start_row=1):
        # رؤوس الأعمدة
        for col, head in enumerate(headers, start=1):
            cell = ws.cell(row=start_row, column=col, value=head)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = center
            cell.border = border
        # الصفوف
        for r, row in enumerate(rows, start=start_row + 1):
            for col, value in enumerate(row, start=1):
                cell = ws.cell(row=r, column=col, value=value)
                cell.alignment = right
                cell.border = border
                if (r - start_row) % 2 == 0:
                    cell.fill = PatternFill("solid", fgColor=_ZEBRA)
        # عرض الأعمدة (تقدير تلقائي)
        for col in range(1, len(headers) + 1):
            letter = get_column_letter(col)
            max_len = len(str(headers[col - 1]))
            for row in rows:
                try:
                    max_len = max(max_len, len(str(row[col - 1])))
                except Exception:
                    pass
            ws.column_dimensions[letter].width = min(60, max(12, max_len + 4))
        ws.freeze_panes = ws.cell(row=start_row + 1, column=1)
        ws.row_dimensions[start_row].height = 24

    # ---------------- ورقة الملخص ----------------
    ws = wb.active
    ws.title = "ملخص"
    _style_sheet_rtl(ws)
    counts = _counts(school)
    ws.cell(row=1, column=1, value="ملف بيانات المدرسة").font = title_font
    ws.cell(row=2, column=1, value="منصة توثيق لتقارير المدارس").font = label_font

    summary_rows = [
        ("اسم المدرسة", getattr(school, "name", "") or ""),
        ("المعرّف (code)", getattr(school, "code", "") or ""),
        ("المدينة", getattr(school, "city", "") or "—"),
        ("المرحلة", getattr(school, "get_stage_display", lambda: "")() or "—"),
        ("النوع", getattr(school, "get_gender_display", lambda: "")() or "—"),
        ("السنة الدراسية الحالية", getattr(school, "current_academic_year", "") or "—"),
        ("عدد التقارير", counts["reports"]),
        ("عدد ملفات الإنجاز", counts["achievements"]),
        ("عدد التذاكر والطلبات", counts["tickets"]),
        ("عدد التعاميم", counts["circulars"]),
        ("عدد الإشعارات", counts["notifications"]),
        ("عدد المعلمين", counts["teachers"]),
        ("عدد الأقسام", counts["departments"]),
        ("تاريخ التصدير", timezone.localtime().strftime("%Y-%m-%d %H:%M")),
    ]
    for i, (label, value) in enumerate(summary_rows, start=4):
        lc = ws.cell(row=i, column=1, value=label)
        lc.font = label_font
        lc.alignment = right
        vc = ws.cell(row=i, column=2, value=value)
        vc.alignment = right
    ws.column_dimensions["A"].width = 28
    ws.column_dimensions["B"].width = 44

    # ---------------- ورقة التقارير ----------------
    ws_reports = wb.create_sheet("التقارير")
    _style_sheet_rtl(ws_reports)
    report_rows = []
    reports_qs = (
        Report.objects.filter(school=school)
        .select_related("teacher", "category")
        .order_by("-report_date", "-id")
    )
    for rep in reports_qs.iterator():
        images = sum(1 for f in (rep.image1, rep.image2, rep.image3, rep.image4) if getattr(f, "name", ""))
        report_rows.append(
            [
                rep.id,
                rep.title or "",
                getattr(rep.category, "name", "") or "—",
                rep.teacher_display_name,
                rep.report_date.strftime("%Y-%m-%d") if rep.report_date else "",
                rep.academic_year or "—",
                rep.beneficiaries_count if rep.beneficiaries_count is not None else "—",
                images,
                (rep.idea or "").strip(),
            ]
        )
    _write_table(
        ws_reports,
        ["#", "العنوان", "النوع", "المنفّذ", "التاريخ", "السنة", "المستفيدون", "عدد الصور", "التفاصيل"],
        report_rows,
    )

    # ---------------- ورقة ملفات الإنجاز ----------------
    ws_ach = wb.create_sheet("ملفات الإنجاز")
    _style_sheet_rtl(ws_ach)
    ach_rows = []
    ach_qs = (
        TeacherAchievementFile.objects.filter(school=school)
        .select_related("teacher")
        .order_by("-academic_year", "teacher__name")
    )
    for ach in ach_qs.iterator():
        ach_rows.append(
            [
                ach.id,
                getattr(ach.teacher, "name", "") or "—",
                ach.academic_year or "—",
                ach.get_status_display() if hasattr(ach, "get_status_display") else "",
                ach.created_at.strftime("%Y-%m-%d") if getattr(ach, "created_at", None) else "",
            ]
        )
    _write_table(
        ws_ach,
        ["#", "المعلم", "السنة الدراسية", "الحالة", "تاريخ الإنشاء"],
        ach_rows,
    )

    # ---------------- ورقة الطلبات والتذاكر ----------------
    ws_tickets = wb.create_sheet("الطلبات والتذاكر")
    _style_sheet_rtl(ws_tickets)
    ticket_rows = []
    for ticket in _tickets_qs(school).iterator(chunk_size=100):
        ticket_rows.append(
            [
                ticket.id,
                ticket.title or "",
                getattr(ticket.creator, "name", "") or "—",
                getattr(ticket.department, "name", "") or "—",
                ticket.get_status_display(),
                ticket.created_at.strftime("%Y-%m-%d %H:%M"),
                "نعم" if getattr(ticket.attachment, "name", "") else "لا",
                (ticket.body or "").strip(),
            ]
        )
    _write_table(
        ws_tickets,
        ["#", "العنوان", "المرسل", "القسم", "الحالة", "التاريخ", "مرفق", "التفاصيل"],
        ticket_rows,
    )

    # ---------------- ورقة التعاميم والإشعارات ----------------
    ws_notifications = wb.create_sheet("التعاميم والإشعارات")
    _style_sheet_rtl(ws_notifications)
    notification_rows = []
    for notification in _notifications_qs(school).iterator(chunk_size=100):
        notification_rows.append(
            [
                notification.id,
                "تعميم" if notification.requires_signature else "إشعار",
                notification.title or "",
                getattr(notification.created_by, "name", "") or "—",
                notification.created_at.strftime("%Y-%m-%d %H:%M"),
                notification.recipients.count(),
                "نعم" if getattr(notification.attachment, "name", "") else "لا",
                (notification.message or "").strip(),
            ]
        )
    _write_table(
        ws_notifications,
        ["#", "النوع", "العنوان", "المرسل", "التاريخ", "المستلمون", "مرفق", "النص"],
        notification_rows,
    )

    # ---------------- ورقة المعلمين ----------------
    ws_teachers = wb.create_sheet("المعلمون")
    _style_sheet_rtl(ws_teachers)
    teacher_rows = []
    memberships = (
        SchoolMembership.objects.filter(school=school)
        .select_related("teacher")
        .order_by("role_type", "teacher__name")
    )
    for m in memberships.iterator():
        teacher_rows.append(
            [
                getattr(m.teacher, "name", "") or "—",
                getattr(m.teacher, "phone", "") or "—",
                m.get_role_type_display() if hasattr(m, "get_role_type_display") else "",
                m.get_job_title_display() if hasattr(m, "get_job_title_display") else "",
                "نشط" if m.is_active else "موقوف",
            ]
        )
    _write_table(
        ws_teachers,
        ["الاسم", "الجوال", "الدور", "المسمى الوظيفي", "الحالة"],
        teacher_rows,
    )

    # ---------------- ورقة الأقسام ----------------
    ws_depts = wb.create_sheet("الأقسام")
    _style_sheet_rtl(ws_depts)
    dept_rows = []
    for dep in Department.objects.filter(school=school).order_by("id").iterator():
        members = DepartmentMembership.objects.filter(department=dep).count()
        dept_rows.append(
            [
                dep.name or "—",
                dep.role_label or "—",
                "نشط" if dep.is_active else "موقوف",
                members,
            ]
        )
    _write_table(
        ws_depts,
        ["اسم القسم", "الدور الظاهر", "الحالة", "عدد الأعضاء"],
        dept_rows,
    )

    return wb


def build_school_export_bytes(school) -> bytes:
    wb = build_school_export_workbook(school)
    buffer = BytesIO()
    wb.save(buffer)
    return buffer.getvalue()


def build_year_archive_index_bytes(
    school,
    academic_year,
    *,
    teacher=None,
    school_wide=True,
) -> bytes:
    """Build the Excel index promised inside every yearly archive package."""
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from .services_archive import UNCLASSIFIED_YEAR

    reports = _reports_qs(
        school,
        academic_year=academic_year,
        teacher=teacher,
        school_wide=school_wide,
    )
    achievements = (
        TeacherAchievementFile.objects.none()
        if academic_year == UNCLASSIFIED_YEAR
        else _achievement_files_qs(
            school,
            academic_year=academic_year,
            teacher=teacher,
            school_wide=school_wide,
        )
    )

    workbook = Workbook()
    report_sheet = workbook.active
    report_sheet.title = "التقارير"
    achievement_sheet = workbook.create_sheet("ملفات الإنجاز")
    sheets = [report_sheet, achievement_sheet]
    ticket_sheet = None
    notification_sheet = None
    if school_wide:
        ticket_sheet = workbook.create_sheet("الطلبات والتذاكر")
        notification_sheet = workbook.create_sheet("التعاميم والإشعارات")
        sheets.extend([ticket_sheet, notification_sheet])
    for sheet in sheets:
        sheet.sheet_view.rightToLeft = True

    report_sheet.append(["المعرف", "العنوان", "المعلم", "التاريخ", "التصنيف", "السنة الدراسية"])
    for report in reports.iterator():
        report_sheet.append(
            [
                report.pk,
                report.title,
                getattr(report, "teacher_display_name", "")
                or getattr(getattr(report, "teacher", None), "name", ""),
                report.report_date.isoformat() if report.report_date else "",
                getattr(getattr(report, "category", None), "name", ""),
                report.academic_year or "غير مصنف",
            ]
        )

    achievement_sheet.append(["المعرف", "المعلم", "السنة الدراسية", "الحالة"])
    for achievement in achievements.iterator():
        achievement_sheet.append(
            [
                achievement.pk,
                getattr(achievement, "teacher_name", "")
                or getattr(getattr(achievement, "teacher", None), "name", ""),
                achievement.academic_year,
                achievement.get_status_display(),
            ]
        )

    if ticket_sheet is not None:
        ticket_sheet.append(["المعرف", "العنوان", "المرسل", "القسم", "الحالة", "تاريخ الإنشاء"])
        for ticket in _tickets_qs(school).iterator(chunk_size=100):
            ticket_sheet.append(
                [
                    ticket.pk,
                    ticket.title,
                    getattr(ticket.creator, "name", ""),
                    getattr(ticket.department, "name", ""),
                    ticket.get_status_display(),
                    ticket.created_at.isoformat() if ticket.created_at else "",
                ]
            )

    if notification_sheet is not None:
        notification_sheet.append(["المعرف", "النوع", "العنوان", "المرسل", "تاريخ الإنشاء", "المستلمون"])
        for notification in _notifications_qs(school).iterator(chunk_size=100):
            notification_sheet.append(
                [
                    notification.pk,
                    "تعميم" if notification.requires_signature else "إشعار",
                    notification.title,
                    getattr(notification.created_by, "name", ""),
                    notification.created_at.isoformat() if notification.created_at else "",
                    notification.recipients.count(),
                ]
            )

    for sheet in sheets:
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor=_HEADER_FILL)
            cell.alignment = Alignment(horizontal="center")
        sheet.freeze_panes = "A2"
        for column, width in {"A": 12, "B": 34, "C": 28, "D": 18, "E": 24, "F": 20}.items():
            sheet.column_dimensions[column].width = width

    buffer = BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def export_summary_counts(school) -> dict:
    """تُستخدم في صفحة المعاينة قبل التنزيل."""
    counts = _counts(school)
    counts["files"] = count_export_files(school)
    return counts


# =====================================================================
# تصدير الملفات الفعلية داخل أرشيف ZIP
# =====================================================================
import os
import re
import zipfile
import tempfile


def export_zip_filename(school) -> str:
    code = (getattr(school, "code", "") or "school").strip() or "school"
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    return f"school-files-{code}-{stamp}.zip"


def _safe_segment(text, *, fallback: str = "ملف", maxlen: int = 60) -> str:
    """يُنظّف نصًّا ليصلح كاسم مجلد/ملف داخل الأرشيف (يدعم العربية)."""
    text = (str(text) if text is not None else "").strip()
    text = re.sub(r'[\\/:*?"<>|\r\n\t]+', " ", text)
    text = re.sub(r"\s+", " ", text).strip(" .")
    if not text:
        text = fallback
    return text[:maxlen].strip() or fallback


def _reports_qs(school, *, academic_year=None, teacher=None, school_wide=True):
    """قائمة تقارير المدرسة ضمن النطاق المطلوب (مدرسة/سنة/معلّم)."""
    from .services_archive import UNCLASSIFIED_YEAR

    qs = (
        Report.objects.filter(school=school)
        .select_related("teacher", "category")
        .order_by("-report_date", "-id")
    )
    if not school_wide and teacher is not None:
        qs = qs.filter(teacher=teacher)
    if academic_year == UNCLASSIFIED_YEAR:
        from django.db.models import Q

        qs = qs.filter(Q(academic_year="") | Q(academic_year__isnull=True))
    elif academic_year:
        qs = qs.filter(academic_year=academic_year)
    return qs


def _report_folder(rep) -> str:
    """مجلد ثابت داخل الأرشيف لتقرير (تُوضَع فيه صوره وملف PDF الخاص به)."""
    day = rep.report_date.strftime("%Y-%m-%d") if rep.report_date else "بدون-تاريخ"
    return "التقارير/" + _safe_segment(f"{day} - {rep.title} - {rep.id}", fallback=f"تقرير-{rep.id}")


def _achievement_files_qs(school, *, academic_year=None, teacher=None, school_wide=True):
    """قائمة ملفات الإنجاز ضمن النطاق المطلوب (مدرسة/سنة/معلّم)."""
    qs = (
        TeacherAchievementFile.objects.filter(school=school)
        .select_related("teacher")
        .order_by("-academic_year", "teacher__name")
    )
    if not school_wide and teacher is not None:
        qs = qs.filter(teacher=teacher)
    if academic_year:
        qs = qs.filter(academic_year=academic_year)
    return qs


def _achievement_arc_name(ach) -> str:
    """مسار ثابت داخل الأرشيف لملف إنجاز (سواء كان مخزّنًا أو مُولّدًا)."""
    tname = _safe_segment(getattr(ach.teacher, "name", "") or "معلم")
    year = _safe_segment(ach.academic_year or "بدون-سنة")
    return f"ملفات-الإنجاز/{tname} - {year}.pdf"


def _tickets_qs(school):
    return (
        Ticket.objects.filter(school=school)
        .select_related("creator", "assignee", "department", "school")
        .prefetch_related("recipients", "notes__author", "images")
        .order_by("-created_at", "-id")
    )


def _notifications_qs(school):
    return (
        Notification.objects.filter(school=school)
        .select_related("created_by", "school")
        .prefetch_related("recipients__teacher")
        .order_by("-created_at", "-id")
    )


def _ticket_folder(ticket) -> str:
    day = ticket.created_at.strftime("%Y-%m-%d") if ticket.created_at else "بدون-تاريخ"
    name = _safe_segment(
        f"{day} - {ticket.title} - {ticket.id}",
        fallback=f"طلب-{ticket.id}",
    )
    return f"الطلبات-والتذاكر/{name}"


def _notification_folder(notification) -> str:
    root = "التعاميم" if notification.requires_signature else "الإشعارات"
    day = (
        notification.created_at.strftime("%Y-%m-%d")
        if notification.created_at
        else "بدون-تاريخ"
    )
    title = notification.title or ((notification.message or "")[:40])
    name = _safe_segment(
        f"{day} - {title} - {notification.id}",
        fallback=f"سجل-{notification.id}",
    )
    return f"{root}/{name}"


def _administrative_file_fields(school):
    """Original attachments/images for school-wide administrative records."""
    for ticket in _tickets_qs(school).iterator(chunk_size=100):
        folder = _ticket_folder(ticket)
        field = getattr(ticket, "attachment", None)
        name = getattr(field, "name", "") or ""
        if name:
            ext = os.path.splitext(name)[1].lower() or ".bin"
            yield f"{folder}/المرفق-الرئيسي{ext}", field
        for index, ticket_image in enumerate(ticket.images.all(), start=1):
            image = getattr(ticket_image, "image", None)
            image_name = getattr(image, "name", "") or ""
            if image_name:
                ext = os.path.splitext(image_name)[1].lower() or ".jpg"
                yield f"{folder}/صورة-{index}{ext}", image

    for notification in _notifications_qs(school).iterator(chunk_size=100):
        field = getattr(notification, "attachment", None)
        name = getattr(field, "name", "") or ""
        if not name:
            continue
        ext = os.path.splitext(name)[1].lower() or ".bin"
        yield f"{_notification_folder(notification)}/المرفق{ext}", field


def _file_fields_for_school(school, *, academic_year=None, teacher=None, school_wide=True):
    """مولّد ينتج (مسار_داخل_الأرشيف، حقل_الملف) لكل ملف فعلي للمدرسة.

    - ``academic_year``: قصر النطاق على سنة محددة (أو السنتينل UNCLASSIFIED لغير المصنّفة).
    - ``teacher`` + ``school_wide=False``: قصر النطاق على ملفات معلّم واحد.
    """
    from .models import AchievementEvidenceImage
    from .services_archive import UNCLASSIFIED_YEAR

    scope_teacher = teacher if not school_wide else None

    # 1) صور التقارير (ملف PDF لكل تقرير يُولَّد لاحقًا في باني الـ ZIP)
    for rep in _reports_qs(
        school, academic_year=academic_year, teacher=scope_teacher, school_wide=school_wide
    ).iterator():
        folder = _report_folder(rep)
        for idx, field in enumerate((rep.image1, rep.image2, rep.image3, rep.image4), start=1):
            name = getattr(field, "name", "") or ""
            if not name:
                continue
            ext = os.path.splitext(name)[1].lower() or ".jpg"
            yield f"{folder}/صورة-{idx}{ext}", field

    # ملفات الإنجاز والشواهد لا تنطبق على "غير المصنّفة بسنة"
    if academic_year == UNCLASSIFIED_YEAR:
        return

    # 2) ملفات الإنجاز (PDF المخزّن فقط؛ غير المخزّن يُولَّد لاحقًا في باني الـ ZIP)
    achievements = _achievement_files_qs(
        school, academic_year=academic_year, teacher=scope_teacher, school_wide=school_wide
    )
    for ach in achievements.iterator():
        field = getattr(ach, "pdf_file", None)
        if not getattr(field, "name", ""):
            continue
        yield _achievement_arc_name(ach), field

    # 3) شواهد ملفات الإنجاز (صور)
    evidence = (
        AchievementEvidenceImage.objects
        .filter(section__file__school=school)
        .select_related("section", "section__file", "section__file__teacher")
    )
    if scope_teacher is not None:
        evidence = evidence.filter(section__file__teacher=scope_teacher)
    if academic_year:
        evidence = evidence.filter(section__file__academic_year=academic_year)
    for ev in evidence.iterator():
        field = getattr(ev, "image", None)
        name = getattr(field, "name", "") or ""
        if not name:
            continue
        try:
            sec = ev.section
            ach_file = sec.file
            tname = _safe_segment(getattr(ach_file.teacher, "name", "") or "معلم")
            year = _safe_segment(getattr(ach_file, "academic_year", "") or "بدون-سنة")
            section = _safe_segment(getattr(sec, "title", "") or getattr(sec, "code", "") or "قسم")
        except Exception:
            tname, year, section = "معلم", "بدون-سنة", "قسم"
        base = os.path.basename(name) or f"شاهد-{ev.id}.jpg"
        yield f"ملفات-الإنجاز/شواهد/{tname} - {year}/{section}/{base}", field

    # السجلات الإدارية لا تحمل سنة دراسية؛ يضيفها أرشيف السنة صراحةً
    # بوصفها "الحالة حتى لحظة إنشاء النسخة". وهنا نضيفها للتصدير الكامل.
    if academic_year or not school_wide:
        return

    yield from _administrative_file_fields(school)


def count_export_files(school, *, academic_year=None, teacher=None, school_wide=True) -> int:
    """عدد الملفات الفعلية التي ستُصدَّر (للعرض في صفحة المعاينة)."""
    try:
        count = sum(
            1 for _ in _file_fields_for_school(
                school, academic_year=academic_year, teacher=teacher, school_wide=school_wide
            )
        )
        if academic_year and school_wide:
            count += sum(1 for _ in _administrative_file_fields(school))
        return count
    except Exception:
        return 0


def archive_zip_filename(school, academic_year=None) -> str:
    code = (getattr(school, "code", "") or "school").strip() or "school"
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    year_part = ""
    if academic_year:
        year_part = "-" + re.sub(r"[^0-9A-Za-z\-]+", "", str(academic_year)) or ""
    return f"archive-{code}{year_part}-{stamp}.zip"


def build_school_export_zip_file(
    school,
    *,
    academic_year=None,
    teacher=None,
    school_wide=True,
    request=None,
    return_metadata=False,
):
    """يبني أرشيف ZIP يحوي ملفات المدرسة الفعلية + فهرس + ملف تحقّق سلامة (SHA-256).

    - عند تمرير ``academic_year`` يصبح أرشيف سنة محددة (مع manifest سلامة للسنة).
    - عند عدم تمريرها يصبح تصدير المدرسة الكامل (مع فهرس Excel).
    - ``request`` (اختياري): يُمرَّر لتوليد PDF ملفات الإنجاز غير المخزّنة لحظيًا.
    - عند ``return_metadata=True`` يعيد ``(file, metadata)`` لإظهار تقرير اكتمال صريح.
    """
    import hashlib
    from .services_archive import UNCLASSIFIED_YEAR, archive_year_label

    is_year_archive = bool(academic_year)
    is_unclassified = academic_year == UNCLASSIFIED_YEAR
    generated_at = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S %Z")
    manifest_lines = []  # (sha256, size, path)
    missing_file_count = 0
    report_count = _reports_qs(
        school,
        academic_year=academic_year,
        teacher=teacher,
        school_wide=school_wide,
    ).count()
    achievement_count = (
        0
        if is_unclassified
        else _achievement_files_qs(
            school,
            academic_year=academic_year,
            teacher=teacher,
            school_wide=school_wide,
        ).count()
    )
    ticket_count = _tickets_qs(school).count() if school_wide else 0
    circular_count = (
        Notification.objects.filter(school=school, requires_signature=True).count()
        if school_wide
        else 0
    )
    notification_count = (
        Notification.objects.filter(school=school, requires_signature=False).count()
        if school_wide
        else 0
    )

    tmp = tempfile.SpooledTemporaryFile(max_size=24 * 1024 * 1024)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        added = 0

        def write_bytes(archive_path: str, data: bytes) -> None:
            nonlocal added
            zf.writestr(archive_path, data)
            manifest_lines.append((hashlib.sha256(data).hexdigest(), len(data), archive_path))
            added += 1

        try:
            if is_year_archive:
                index_bytes = build_year_archive_index_bytes(
                    school,
                    academic_year,
                    teacher=teacher,
                    school_wide=school_wide,
                )
                write_bytes("فهرس-السنة.xlsx", index_bytes)
            else:
                write_bytes("ملخص-البيانات.xlsx", build_school_export_bytes(school))
        except Exception:
            missing_file_count += 1

        for arc_path, field in _file_fields_for_school(
            school, academic_year=academic_year, teacher=teacher, school_wide=school_wide
        ):
            try:
                field.open("rb")
                try:
                    data = field.read()
                finally:
                    try:
                        field.close()
                    except Exception:
                        pass
                write_bytes(arc_path, data)
            except Exception:
                missing_file_count += 1

        # السجلات الإدارية غير مرتبطة بسنة دراسية في قاعدة البيانات. في النسخة
        # السنوية للمدرسة نحفظ حالتها كاملة حتى لحظة إنشاء النسخة.
        if is_year_archive and school_wide:
            for arc_path, field in _administrative_file_fields(school):
                try:
                    field.open("rb")
                    try:
                        data = field.read()
                    finally:
                        try:
                            field.close()
                        except Exception:
                            pass
                    write_bytes(arc_path, data)
                except Exception:
                    missing_file_count += 1

        weasy_unavailable = False
        gen_ok = 0
        gen_failed = 0
        rep_ok = 0
        rep_failed = 0
        ticket_pdf_ok = 0
        ticket_pdf_failed = 0
        notification_pdf_ok = 0
        notification_pdf_failed = 0

        # (أ) ملف PDF لكل تقرير (لكل النطاقات بما فيها غير المصنّفة بسنة)
        for rep in _reports_qs(
            school, academic_year=academic_year, teacher=teacher, school_wide=school_wide
        ).iterator():
            if weasy_unavailable:
                rep_failed += 1
                continue
            try:
                from .pdf_report import generate_report_pdf

                pdf_bytes, _fn = generate_report_pdf(request=request, report=rep)
            except OSError:
                weasy_unavailable = True
                rep_failed += 1
                continue
            except Exception:
                rep_failed += 1
                continue
            arc = f"{_report_folder(rep)}/التقرير.pdf"
            write_bytes(arc, pdf_bytes)
            rep_ok += 1

        # (ب) ملفات الإنجاز غير المخزّنة (لا تنطبق على غير المصنّفة بسنة)
        if not is_unclassified:
            ach_qs = _achievement_files_qs(
                school, academic_year=academic_year, teacher=teacher, school_wide=school_wide
            )
            for ach in ach_qs.iterator():
                if getattr(getattr(ach, "pdf_file", None), "name", ""):
                    continue  # مخزّن مسبقًا وأُدرِج في المرور الأساسي
                if weasy_unavailable:
                    gen_failed += 1
                    continue
                try:
                    from .pdf_achievement import generate_achievement_pdf

                    pdf_bytes, _fn = generate_achievement_pdf(request=request, ach_file=ach)
                except OSError:
                    weasy_unavailable = True
                    gen_failed += 1
                    continue
                except Exception:
                    gen_failed += 1
                    continue
                arc = _achievement_arc_name(ach)
                write_bytes(arc, pdf_bytes)
                gen_ok += 1

        # (ج) PDF موثق لكل طلب/تذكرة ولكل تعميم/إشعار، مع سجل الحالات
        # والمستلمين والتوقيعات. تُحفظ المرفقات الأصلية بجانبه.
        if school_wide:
            from .pdf_archive_records import (
                generate_notification_archive_pdf,
                generate_ticket_archive_pdf,
            )

            for ticket in _tickets_qs(school).iterator(chunk_size=100):
                if weasy_unavailable:
                    ticket_pdf_failed += 1
                    continue
                try:
                    pdf_bytes = generate_ticket_archive_pdf(ticket, request=request)
                except OSError:
                    weasy_unavailable = True
                    ticket_pdf_failed += 1
                    continue
                except Exception:
                    ticket_pdf_failed += 1
                    continue
                write_bytes(f"{_ticket_folder(ticket)}/سجل-الطلب.pdf", pdf_bytes)
                ticket_pdf_ok += 1

            for notification in _notifications_qs(school).iterator(chunk_size=100):
                if weasy_unavailable:
                    notification_pdf_failed += 1
                    continue
                try:
                    pdf_bytes = generate_notification_archive_pdf(
                        notification,
                        request=request,
                    )
                except OSError:
                    weasy_unavailable = True
                    notification_pdf_failed += 1
                    continue
                except Exception:
                    notification_pdf_failed += 1
                    continue
                write_bytes(
                    f"{_notification_folder(notification)}/السجل.pdf",
                    pdf_bytes,
                )
                notification_pdf_ok += 1

        content_notes = [
            "تقرير اكتمال الحزمة:",
            f"  • التقارير في النطاق: {report_count}",
            f"  • ملفات الإنجاز في النطاق: {achievement_count}",
            f"  • الطلبات والتذاكر حتى لحظة إنشاء النسخة: {ticket_count}",
            f"  • التعاميم حتى لحظة إنشاء النسخة: {circular_count}",
            f"  • الإشعارات حتى لحظة إنشاء النسخة: {notification_count}",
            f"  • ملفات PDF للتقارير التي تم توليدها: {rep_ok}",
            f"  • ملفات PDF لملفات الإنجاز التي تم توليدها: {gen_ok}",
            f"  • ملفات PDF للطلبات والتذاكر التي تم توليدها: {ticket_pdf_ok}",
            f"  • ملفات PDF للتعاميم والإشعارات التي تم توليدها: {notification_pdf_ok}",
        ]
        if missing_file_count:
            content_notes.append(f"  ⚠ ملفات أصلية تعذرت قراءتها: {missing_file_count}")
        if rep_failed:
            content_notes.append(f"  ⚠ ملفات PDF للتقارير تعذر توليدها: {rep_failed}")
        if gen_failed:
            content_notes.append(f"  ⚠ ملفات PDF لملفات الإنجاز تعذر توليدها: {gen_failed}")
        if ticket_pdf_failed:
            content_notes.append(
                f"  ⚠ ملفات PDF للطلبات والتذاكر تعذر توليدها: {ticket_pdf_failed}"
            )
        if notification_pdf_failed:
            content_notes.append(
                "  ⚠ ملفات PDF للتعاميم والإشعارات تعذر توليدها: "
                f"{notification_pdf_failed}"
            )
        if not (
            missing_file_count
            or rep_failed
            or gen_failed
            or ticket_pdf_failed
            or notification_pdf_failed
        ):
            content_notes.append("  ✓ اكتمل إنشاء الحزمة دون ملفات مفقودة أو أخطاء توليد.")
        if school_wide:
            content_notes.append(
                "  • ملاحظة النطاق: السجلات الإدارية لا تحمل سنة دراسية؛ "
                "لذلك تم حفظ حالتها كاملة حتى لحظة إنشاء النسخة."
            )
        content_notes.append("=" * 60)

        scope = archive_year_label(academic_year) if is_year_archive else "كل السنوات"
        header = [
            "ملف تحقّق وسلامة الأرشيف — منصة توثيق لتقارير المدارس",
            "=" * 60,
            f"المدرسة      : {getattr(school, 'name', '') or ''} ({getattr(school, 'code', '') or ''})",
            f"النطاق       : {scope}",
            f"تاريخ الإنشاء: {generated_at}",
            f"عدد الملفات  : {added}",
            "خوارزمية البصمة: SHA-256",
            "ملاحظة: هذا الأرشيف نسخة للقراءة فقط؛ يمكن التحقق من سلامة كل ملف عبر بصمته أدناه.",
            "=" * 60,
            *content_notes,
            "",
            "البصمة (SHA-256)".ljust(66) + "الحجم(بايت)".ljust(14) + "المسار",
        ]
        body = [f"{h}  {str(size).ljust(12)}  {path}" for (h, size, path) in manifest_lines]
        zf.writestr("الفهرس-والتحقق.txt", "\n".join(header + body) + "\n")

        if (
            report_count == 0
            and achievement_count == 0
            and ticket_count == 0
            and circular_count == 0
            and notification_count == 0
        ):
            zf.writestr(
                "اقرأني.txt",
                "لا توجد تقارير أو ملفات إنجاز ضمن هذه السنة وقت إنشاء النسخة.\n",
            )

    tmp.seek(0)
    digest = hashlib.sha256()
    archive_size = 0
    while True:
        chunk = tmp.read(1024 * 1024)
        if not chunk:
            break
        digest.update(chunk)
        archive_size += len(chunk)
    tmp.seek(0)
    metadata = {
        "generated_at": generated_at,
        "file_count": added,
        "missing_file_count": missing_file_count,
        "failed_pdf_count": (
            rep_failed
            + gen_failed
            + ticket_pdf_failed
            + notification_pdf_failed
        ),
        "generated_report_pdf_count": rep_ok,
        "generated_achievement_pdf_count": gen_ok,
        "report_count": report_count,
        "achievement_count": achievement_count,
        "ticket_count": ticket_count,
        "circular_count": circular_count,
        "notification_count": notification_count,
        "archive_size_bytes": archive_size,
        "archive_sha256": digest.hexdigest(),
        "is_partial": bool(
            missing_file_count
            or rep_failed
            or gen_failed
            or ticket_pdf_failed
            or notification_pdf_failed
        ),
        "notes": "\n".join(content_notes),
    }
    return (tmp, metadata) if return_metadata else tmp
