from __future__ import annotations

from typing import Iterable

from django.db.models import Count, Q

from .models import (
    AchievementEvidenceImage,
    AchievementEvidenceReport,
    Report,
    School,
    SchoolArchiveAddon,
    TeacherAchievementFile,
    school_has_archive_addon,
)

UNCLASSIFIED_YEAR = "__unclassified__"


def _year_sort_key(value: str) -> tuple[int, str]:
    value = (value or "").strip()
    try:
        return (int(value.split("-", 1)[0]), value)
    except Exception:
        return (-1, value)


def _clean_years(values: Iterable[str]) -> list[str]:
    years = {str(v).strip() for v in values if str(v or "").strip()}
    return sorted(years, key=_year_sort_key, reverse=True)


def archive_year_label(value: str) -> str:
    if value == UNCLASSIFIED_YEAR:
        return "تقارير غير مصنفة بسنة"
    return f"{value} هـ"


def school_archive_enabled(school: School | None) -> bool:
    return school_has_archive_addon(school)


def _file_size(value) -> int:
    if not value:
        return 0
    try:
        if not getattr(value, "name", ""):
            return 0
    except Exception:
        return 0
    try:
        return int(value.size or 0)
    except Exception:
        return 0


def _incoming_size(files) -> int:
    total = 0
    for file_obj in files or []:
        try:
            total += int(getattr(file_obj, "size", 0) or 0)
        except Exception:
            pass
    return total


def calculate_school_archive_storage_bytes(school: School | None) -> int:
    """Best-effort sum of files covered by the archive add-on."""
    if school is None:
        return 0

    total = 0
    for report in Report.objects.filter(school=school).only("image1", "image2", "image3", "image4"):
        total += _file_size(report.image1)
        total += _file_size(report.image2)
        total += _file_size(report.image3)
        total += _file_size(report.image4)

    for ach_file in TeacherAchievementFile.objects.filter(school=school).only("pdf_file"):
        total += _file_size(ach_file.pdf_file)

    evidence_images = AchievementEvidenceImage.objects.filter(section__file__school=school).only("image")
    for evidence in evidence_images:
        total += _file_size(evidence.image)

    evidence_reports = AchievementEvidenceReport.objects.filter(section__file__school=school).only(
        "archived_image1",
        "archived_image2",
        "archived_image3",
        "archived_image4",
    )
    for evidence in evidence_reports:
        total += _file_size(evidence.archived_image1)
        total += _file_size(evidence.archived_image2)
        total += _file_size(evidence.archived_image3)
        total += _file_size(evidence.archived_image4)

    return total


def sync_school_archive_storage_usage(school: School | None) -> int:
    if school is None:
        return 0
    try:
        addon = SchoolArchiveAddon.objects.get(school=school)
    except SchoolArchiveAddon.DoesNotExist:
        return 0

    used = calculate_school_archive_storage_bytes(school)
    if addon.storage_used_bytes != used:
        addon.storage_used_bytes = used
        addon.save(update_fields=["storage_used_bytes", "updated_at"])
    return used


def archive_storage_capacity_error(school: School | None, incoming_files, *, replacing_files=None) -> str:
    """Return an Arabic error message when an active archive add-on exceeds its quota."""
    if school is None:
        return ""
    try:
        addon = SchoolArchiveAddon.objects.get(school=school)
    except SchoolArchiveAddon.DoesNotExist:
        return ""
    if not addon.is_active:
        return ""

    incoming_bytes = _incoming_size(incoming_files)
    if incoming_bytes <= 0:
        return ""

    used_bytes = calculate_school_archive_storage_bytes(school)
    replaced_bytes = sum(_file_size(value) for value in (replacing_files or []))
    projected_used_bytes = max(0, used_bytes - replaced_bytes) + incoming_bytes
    limit_bytes = int(addon.storage_limit_gb or 0) * 1024 * 1024 * 1024
    if limit_bytes <= 0 or projected_used_bytes <= limit_bytes:
        return ""

    used_gb = round(used_bytes / (1024 ** 3), 2)
    replaced_gb = round(replaced_bytes / (1024 ** 3), 2)
    incoming_gb = round(incoming_bytes / (1024 ** 3), 2)
    replaced_text = f"، وسيتم استبدال {replaced_gb}GB" if replaced_bytes else ""
    return (
        f"مساحة الأرشيف غير كافية. المستخدم حالياً {used_gb}GB، "
        f"والملفات الجديدة {incoming_gb}GB{replaced_text}، والحد المتاح {addon.storage_limit_gb}GB. "
        "يمكنك طلب زيادة المساحة من صفحة الاشتراك."
    )


def archive_available_years(*, school: School, teacher=None, school_wide: bool = False) -> list[str]:
    years: set[str] = set()

    try:
        if school.current_academic_year:
            years.add(str(school.current_academic_year).strip())
    except Exception:
        pass

    try:
        years.update(str(y).strip() for y in (school.allowed_academic_years or []) if str(y).strip())
    except Exception:
        pass

    reports_qs = Report.objects.filter(school=school)
    achievements_qs = TeacherAchievementFile.objects.filter(school=school)
    if not school_wide and teacher is not None:
        reports_qs = reports_qs.filter(teacher=teacher)
        achievements_qs = achievements_qs.filter(teacher=teacher)

    years.update(reports_qs.exclude(academic_year="").values_list("academic_year", flat=True).distinct())
    years.update(achievements_qs.values_list("academic_year", flat=True).distinct())

    sorted_years = _clean_years(years)
    if reports_qs.filter(Q(academic_year="") | Q(academic_year__isnull=True)).exists():
        sorted_years.append(UNCLASSIFIED_YEAR)
    return sorted_years


def archive_payload(*, school: School, selected_year: str, teacher=None, school_wide: bool = False) -> dict:
    reports_qs = (
        Report.objects.select_related("teacher", "category", "school")
        .filter(school=school)
        .order_by("-report_date", "-id")
    )
    achievements_qs = (
        TeacherAchievementFile.objects.select_related("teacher", "school", "decided_by")
        .filter(school=school)
        .order_by("-academic_year", "teacher__name", "-id")
    )

    if not school_wide and teacher is not None:
        reports_qs = reports_qs.filter(teacher=teacher)
        achievements_qs = achievements_qs.filter(teacher=teacher)

    if selected_year == UNCLASSIFIED_YEAR:
        reports_qs = reports_qs.filter(Q(academic_year="") | Q(academic_year__isnull=True))
        achievements_qs = achievements_qs.none()
    elif selected_year:
        reports_qs = reports_qs.filter(academic_year=selected_year)
        achievements_qs = achievements_qs.filter(academic_year=selected_year)

    report_stats = reports_qs.aggregate(
        total=Count("id"),
        with_images=Count(
            "id",
            filter=Q(image1__gt="") | Q(image2__gt="") | Q(image3__gt="") | Q(image4__gt=""),
        ),
    )
    achievement_stats = achievements_qs.aggregate(
        total=Count("id"),
        approved=Count("id", filter=Q(status=TeacherAchievementFile.Status.APPROVED)),
        submitted=Count("id", filter=Q(status=TeacherAchievementFile.Status.SUBMITTED)),
    )

    return {
        "reports_qs": reports_qs,
        "achievement_files_qs": achievements_qs,
        "report_stats": {
            "total": int(report_stats.get("total") or 0),
            "with_images": int(report_stats.get("with_images") or 0),
        },
        "achievement_stats": {
            "total": int(achievement_stats.get("total") or 0),
            "approved": int(achievement_stats.get("approved") or 0),
            "submitted": int(achievement_stats.get("submitted") or 0),
        },
    }
