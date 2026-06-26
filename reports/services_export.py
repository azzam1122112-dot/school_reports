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
    Report,
    SchoolMembership,
    TeacherAchievementFile,
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

    # الطلبات/التذاكر والتعاميم/الإشعارات لا ترتبط بسنة دراسية،
    # لذا تُضمَّن فقط في التصدير الكامل للمدرسة (وليس أرشيف سنة أو معلّم).
    if academic_year or not school_wide:
        return

    # 4) مرفقات الطلبات والتذاكر (PDF/صور/مستندات)
    from .models import Notification, Ticket

    tickets = (
        Ticket.objects.filter(school=school)
        .select_related("creator", "department")
        .order_by("-created_at")
    )
    for tk in tickets.iterator():
        field = getattr(tk, "attachment", None)
        name = getattr(field, "name", "") or ""
        if not name:
            continue
        ext = os.path.splitext(name)[1].lower()
        day = tk.created_at.strftime("%Y-%m-%d") if getattr(tk, "created_at", None) else "بدون-تاريخ"
        fname = _safe_segment(f"{day} - {tk.title} - {tk.id}", fallback=f"طلب-{tk.id}")
        yield f"الطلبات-والتذاكر/{fname}{ext}", field

    # 5) مرفقات التعاميم والإشعارات (PDF/صور)
    notifications = Notification.objects.filter(school=school).order_by("-created_at")
    for note in notifications.iterator():
        field = getattr(note, "attachment", None)
        name = getattr(field, "name", "") or ""
        if not name:
            continue
        ext = os.path.splitext(name)[1].lower()
        folder = "التعاميم" if getattr(note, "requires_signature", False) else "الإشعارات"
        day = note.created_at.strftime("%Y-%m-%d") if getattr(note, "created_at", None) else "بدون-تاريخ"
        title = note.title or ((note.message or "")[:40])
        fname = _safe_segment(f"{day} - {title} - {note.id}", fallback=f"تعميم-{note.id}")
        yield f"{folder}/{fname}{ext}", field


def count_export_files(school, *, academic_year=None, teacher=None, school_wide=True) -> int:
    """عدد الملفات الفعلية التي ستُصدَّر (للعرض في صفحة المعاينة)."""
    try:
        return sum(
            1 for _ in _file_fields_for_school(
                school, academic_year=academic_year, teacher=teacher, school_wide=school_wide
            )
        )
    except Exception:
        return 0


def archive_zip_filename(school, academic_year=None) -> str:
    code = (getattr(school, "code", "") or "school").strip() or "school"
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M")
    year_part = ""
    if academic_year:
        year_part = "-" + re.sub(r"[^0-9A-Za-z\-]+", "", str(academic_year)) or ""
    return f"archive-{code}{year_part}-{stamp}.zip"


def build_school_export_zip_file(school, *, academic_year=None, teacher=None, school_wide=True, request=None):
    """يبني أرشيف ZIP يحوي ملفات المدرسة الفعلية + فهرس + ملف تحقّق سلامة (SHA-256).

    - عند تمرير ``academic_year`` يصبح أرشيف سنة محددة (مع manifest سلامة للسنة).
    - عند عدم تمريرها يصبح تصدير المدرسة الكامل (مع فهرس Excel).
    - ``request`` (اختياري): يُمرَّر لتوليد PDF ملفات الإنجاز غير المخزّنة لحظيًا.
    - يُعيد كائنًا شبيهًا بملف (SpooledTemporaryFile) مؤشّره عند البداية، صالحًا للبثّ.
    """
    import hashlib
    from .services_archive import UNCLASSIFIED_YEAR

    is_year_archive = bool(academic_year)
    is_unclassified = academic_year == UNCLASSIFIED_YEAR
    generated_at = timezone.localtime().strftime("%Y-%m-%d %H:%M:%S %Z")
    manifest_lines = []  # (sha256, size, path)

    tmp = tempfile.SpooledTemporaryFile(max_size=24 * 1024 * 1024)
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=1) as zf:
        # فهرس Excel في جذر الأرشيف (للتصدير الكامل)
        if not is_year_archive:
            try:
                zf.writestr("ملخص-البيانات.xlsx", build_school_export_bytes(school))
            except Exception:
                pass

        added = 0
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
                zf.writestr(arc_path, data)
                manifest_lines.append((hashlib.sha256(data).hexdigest(), len(data), arc_path))
                added += 1
            except Exception:
                # ملف مفقود من التخزين أو تعذّرت قراءته — نتخطّاه دون كسر التصدير
                continue

        # توليد PDF (server-side) مع تجاهل آمن عند الفشل.
        # weasy_unavailable: عند فشل مكتبات الطباعة نوقف بقية المحاولات لتفادي البطء.
        weasy_unavailable = False
        gen_ok = 0           # ملفات إنجاز مُولّدة
        gen_failed = 0
        rep_ok = 0           # تقارير مُولّدة كـ PDF
        rep_failed = 0

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
            zf.writestr(arc, pdf_bytes)
            manifest_lines.append((hashlib.sha256(pdf_bytes).hexdigest(), len(pdf_bytes), arc))
            added += 1
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
                zf.writestr(arc, pdf_bytes)
                manifest_lines.append((hashlib.sha256(pdf_bytes).hexdigest(), len(pdf_bytes), arc))
                added += 1
                gen_ok += 1

        # ملاحظات المحتوى (للتصدير الكامل): تشرح ما يحتويه الأرشيف وما قد لا يظهر ولماذا.
        content_notes = []
        if not is_year_archive:
            content_notes = [
                "محتوى الأرشيف:",
                "  • التقارير: لكل تقرير ملف PDF رسمي (التقرير.pdf) + صوره الأصلية داخل مجلده.",
            ]
            if rep_ok:
                content_notes.append(f"    ✓ وُلِّد {rep_ok} ملف PDF للتقارير أثناء التصدير.")
            if rep_failed:
                content_notes.append(
                    f"    ⚠ تعذّر توليد {rep_failed} ملف PDF للتقارير "
                    "(قد تكون مكتبات الطباعة غير متوفّرة على هذا الخادم)."
                )
            content_notes.append("  • ملفات الإنجاز: تُدرَج بصيغة PDF (تُولَّد تلقائيًا أثناء التصدير عند الحاجة).")
            if gen_ok:
                content_notes.append(f"    ✓ وُلِّد {gen_ok} ملف PDF لملفات الإنجاز أثناء التصدير.")
            if gen_failed:
                content_notes.append(
                    f"    ⚠ تعذّر توليد {gen_failed} ملف PDF لملفات الإنجاز "
                    "(قد تكون مكتبات الطباعة غير متوفّرة على هذا الخادم)."
                )
            content_notes.append("  • مرفقات الطلبات/التذاكر والتعاميم/الإشعارات: تُدرَج إن كان لها مرفق مرفوع.")
            content_notes.append("=" * 60)

        # ملف تحقّق السلامة (معيار حفظ عالمي: بصمات SHA-256 لكل ملف)
        scope = (f"السنة الدراسية {academic_year} هـ" if is_year_archive else "كل السنوات")
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

        if added == 0:
            zf.writestr(
                "اقرأني.txt",
                "لا توجد ملفات (صور تقارير أو ملفات إنجاز) ضمن هذا النطاق.\n",
            )

    tmp.seek(0)
    return tmp
