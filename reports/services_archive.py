from __future__ import annotations

from typing import Iterable

from django.db.models import Count, Q, Sum
from django.utils import timezone

from .models import (
    AchievementEvidenceImage,
    AchievementEvidenceReport,
    LeadershipEvidenceImage,
    Notification,
    Report,
    School,
    SchoolArchiveAddon,
    SchoolYearArchive,
    SchoolLeadershipPortfolio,
    TeacherAchievementFile,
    Ticket,
    TicketImage,
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

    for evidence in LeadershipEvidenceImage.objects.filter(
        section__portfolio__school=school
    ).only("image"):
        total += _file_size(evidence.image)

    for year_archive in SchoolYearArchive.objects.filter(school=school).only("archive_file"):
        total += _file_size(year_archive.archive_file)

    for ticket in Ticket.objects.filter(school=school).only("attachment"):
        total += _file_size(ticket.attachment)

    for ticket_image in TicketImage.objects.filter(ticket__school=school).only("image"):
        total += _file_size(ticket_image.image)

    for notification in Notification.objects.filter(school=school).only("attachment"):
        total += _file_size(notification.attachment)

    return total


def school_storage_breakdown(school: School | None) -> dict:
    """Fast manager-facing breakdown from incrementally maintained byte fields."""
    if school is None:
        return {
            "reports": 0,
            "achievements": 0,
            "leadership": 0,
            "tickets": 0,
            "circulars": 0,
            "notifications": 0,
            "snapshots": 0,
            "total": 0,
        }

    def _sum(queryset) -> int:
        return int(queryset.aggregate(total=Sum("storage_bytes")).get("total") or 0)

    circulars_qs = Notification.objects.filter(school=school, requires_signature=True)
    notifications_qs = Notification.objects.filter(school=school, requires_signature=False)
    values = {
        "reports": _sum(Report.objects.filter(school=school)),
        "achievements": (
            _sum(TeacherAchievementFile.objects.filter(school=school))
            + _sum(AchievementEvidenceImage.objects.filter(section__file__school=school))
            + _sum(AchievementEvidenceReport.objects.filter(section__file__school=school))
        ),
        "leadership": _sum(
            LeadershipEvidenceImage.objects.filter(section__portfolio__school=school)
        ),
        "tickets": (
            _sum(Ticket.objects.filter(school=school))
            + _sum(TicketImage.objects.filter(ticket__school=school))
        ),
        "circulars": _sum(circulars_qs),
        "notifications": _sum(notifications_qs),
        "snapshots": _sum(SchoolYearArchive.objects.filter(school=school)),
    }
    values["total"] = sum(values.values())
    return values


def school_administrative_archive_stats(school: School | None) -> dict:
    """School-wide records included as-of snapshot time (they have no academic-year field)."""
    if school is None:
        return {
            "tickets": 0,
            "circulars": 0,
            "notifications": 0,
            "system_notifications": 0,
            "user_notifications": 0,
            "total": 0,
        }
    notifications = Notification.objects.filter(school=school)
    values = {
        "tickets": Ticket.objects.filter(school=school).count(),
        "circulars": notifications.filter(requires_signature=True).count(),
        "notifications": notifications.filter(requires_signature=False).count(),
        "system_notifications": notifications.filter(
            requires_signature=False,
            created_by__isnull=True,
        ).count(),
        "user_notifications": notifications.filter(
            requires_signature=False,
            created_by__isnull=False,
        ).count(),
    }
    values["total"] = values["tickets"] + values["circulars"] + values["notifications"]
    return values


def school_administrative_archive_payload(
    school: School | None,
    *,
    search: str = "",
) -> dict:
    """Detailed school-wide administrative records shown before snapshot creation.

    Administrative records currently have no academic-year field, so the same
    as-of list is included in every school-wide yearly snapshot.
    """
    if school is None:
        return {
            "tickets_qs": Ticket.objects.none(),
            "circulars_qs": Notification.objects.none(),
            "notifications_qs": Notification.objects.none(),
            "matches": {"tickets": 0, "circulars": 0, "notifications": 0, "total": 0},
        }

    tickets_qs = (
        Ticket.objects.filter(school=school)
        .select_related("creator", "assignee", "department")
        .prefetch_related("recipients")
        .order_by("-created_at", "-id")
    )
    notification_base = (
        Notification.objects.filter(school=school)
        .select_related("created_by")
        .prefetch_related("recipients__teacher")
        .order_by("-created_at", "-id")
    )
    circulars_qs = notification_base.filter(requires_signature=True)
    notifications_qs = notification_base.filter(requires_signature=False)

    search = (search or "").strip()
    if search:
        tickets_qs = tickets_qs.filter(
            Q(title__icontains=search)
            | Q(body__icontains=search)
            | Q(creator__name__icontains=search)
            | Q(creator__phone__icontains=search)
            | Q(assignee__name__icontains=search)
            | Q(department__name__icontains=search)
            | Q(recipients__name__icontains=search)
        ).distinct()
        notification_filter = (
            Q(title__icontains=search)
            | Q(message__icontains=search)
            | Q(created_by__name__icontains=search)
            | Q(created_by__phone__icontains=search)
            | Q(recipients__teacher__name__icontains=search)
            | Q(recipients__teacher__phone__icontains=search)
        )
        circulars_qs = circulars_qs.filter(notification_filter).distinct()
        notifications_qs = notifications_qs.filter(notification_filter).distinct()

    matches = {
        "tickets": tickets_qs.count(),
        "circulars": circulars_qs.count(),
        "notifications": notifications_qs.count(),
    }
    matches["total"] = matches["tickets"] + matches["circulars"] + matches["notifications"]
    return {
        "tickets_qs": tickets_qs,
        "circulars_qs": circulars_qs,
        "notifications_qs": notifications_qs,
        "matches": matches,
    }


def recompute_school_storage(school: School | None) -> int:
    """يعيد حساب التخزين الفعلي للمدرسة (مسح كامل دقيق) ويخزّنه على المدرسة.

    يُستخدم للتهيئة الأولية (backfill) أو المصالحة عند الاشتباه بانحراف.
    قد يقرأ أحجام الملفات من التخزين (شبكة) — لذا لا يُستدعى في المسار الساخن.
    """
    if school is None:
        return 0
    used = calculate_school_archive_storage_bytes(school)
    try:
        School.objects.filter(pk=school.pk).update(storage_used_bytes=used)
    except Exception:
        pass
    # مزامنة نسخة الإضافة إن وُجدت (للتوافق الخلفي)
    try:
        addon = SchoolArchiveAddon.objects.get(school=school)
        if addon.storage_used_bytes != used:
            addon.storage_used_bytes = used
            addon.save(update_fields=["storage_used_bytes", "updated_at"])
    except SchoolArchiveAddon.DoesNotExist:
        pass
    return used


def sync_school_archive_storage_usage(school: School | None) -> int:
    """توافق خلفي: يحدّث نسخة الإضافة من الإجمالي التزايدي المخزّن (بلا مسح شبكي)."""
    if school is None:
        return 0
    used = int(
        School.objects.filter(pk=school.pk)
        .values_list("storage_used_bytes", flat=True)
        .first()
        or 0
    )
    try:
        addon = SchoolArchiveAddon.objects.get(school=school)
        if addon.storage_used_bytes != used:
            addon.storage_used_bytes = used
            addon.save(update_fields=["storage_used_bytes", "updated_at"])
    except SchoolArchiveAddon.DoesNotExist:
        pass
    return used


def _platform_free_storage_bytes() -> int:
    """حد التخزين المجاني الأساسي لكل مدرسة (بالبايت) من إعدادات المنصة.

    0 = غير محدود.
    """
    try:
        from .models import PlatformSettings

        mb = int(getattr(PlatformSettings.get_solo(), "free_storage_mb", 0) or 0)
    except Exception:
        mb = 0
    return max(0, mb) * 1024 * 1024


def school_storage_limit_bytes(school: School | None) -> int:
    """الحد الفعلي لتخزين المدرسة (بالبايت).

    - إن كانت لديها إضافة أرشفة مفعّلة: حدّ الإضافة (storage_limit_gb).
    - وإلا: الحد المجاني الأساسي من إعدادات المنصة (free_storage_mb).
    - 0 يعني غير محدود.
    """
    if school is None:
        return 0
    try:
        addon = SchoolArchiveAddon.objects.get(school=school)
        if addon.is_active:
            return int(addon.storage_limit_gb or 0) * 1024 * 1024 * 1024
    except SchoolArchiveAddon.DoesNotExist:
        pass
    return _platform_free_storage_bytes()


def _expired_archive_addon(school: School | None):
    """Return the school's archive add-on when it lapsed on its end date.

    Distinguishes "never bought it" from "bought it and it ran out", which need
    opposite advice.
    """
    if school is None:
        return None
    try:
        addon = SchoolArchiveAddon.objects.filter(school=school).first()
    except Exception:
        return None
    if addon is None or addon.is_active:
        return None
    if not addon.end_date or addon.end_date >= timezone.localdate():
        # Disabled by an administrator rather than lapsed.
        return None
    return addon


def _human_size(num_bytes: int) -> str:
    """تنسيق دقيق للحجم: بايت/كيلو/ميجا/جيجا حسب المقدار."""
    b = max(0, int(num_bytes or 0))
    if b < 1024:
        return f"{b} بايت"
    kb = b / 1024
    if kb < 1024:
        return f"{round(kb, 1)}KB"
    mb = kb / 1024
    if mb < 1024:
        return f"{round(mb, 1)}MB"
    return f"{round(mb / 1024, 2)}GB"


def school_storage_overview(school: School | None) -> dict:
    """Single source of truth for manager storage cards."""
    if school is None:
        used = 0
    else:
        used = int(
            School.objects.filter(pk=school.pk)
            .values_list("storage_used_bytes", flat=True)
            .first()
            or 0
        )
    limit = school_storage_limit_bytes(school)
    is_unlimited = limit <= 0
    percent = 0 if is_unlimited else min(100, round((used / limit) * 100, 1))
    remaining = 0 if is_unlimited else max(0, limit - used)
    breakdown = school_storage_breakdown(school)
    return {
        "used_bytes": used,
        "limit_bytes": limit,
        "remaining_bytes": remaining,
        "used_label": _human_size(used),
        "limit_label": "غير محدود" if is_unlimited else _human_size(limit),
        "remaining_label": "غير محدود" if is_unlimited else _human_size(remaining),
        "usage_percent": percent,
        "is_unlimited": is_unlimited,
        "breakdown": {
            key: {"bytes": value, "label": _human_size(value)}
            for key, value in breakdown.items()
            if key != "total"
        },
    }


def archive_storage_capacity_error(school: School | None, incoming_files, *, replacing_files=None) -> str:
    """رسالة خطأ عربية عند تجاوز حدّ تخزين المدرسة.

    يُطبَّق على جميع المدارس: حدّ إضافة الأرشفة إن كانت مفعّلة، وإلا الحدّ المجاني
    الأساسي من إعدادات المنصة. الحساب يعتمد على الحجم الفعلي للملفات (دقيق).
    """
    if school is None:
        return ""

    incoming_bytes = _incoming_size(incoming_files)
    if incoming_bytes <= 0:
        return ""

    limit_bytes = school_storage_limit_bytes(school)
    if limit_bytes <= 0:
        # 0 = غير محدود
        return ""

    # الحجم المستخدم يُقرأ من الإجمالي التزايدي المخزّن (بلا أي طلب شبكي إلى التخزين).
    # يُحدَّث هذا الإجمالي تلقائيًا عبر إشارات storage_tracking عند كل رفع/حذف.
    used_bytes = int(
        School.objects.filter(pk=school.pk)
        .values_list("storage_used_bytes", flat=True)
        .first()
        or 0
    )
    replaced_bytes = sum(_file_size(value) for value in (replacing_files or []))
    projected_used_bytes = max(0, used_bytes - replaced_bytes) + incoming_bytes
    if projected_used_bytes <= limit_bytes:
        return ""

    has_active_addon = school_has_archive_addon(school)
    replaced_text = f"، وسيتم استبدال {_human_size(replaced_bytes)}" if replaced_bytes else ""
    base = (
        f"تم تجاوز حد التخزين المتاح للمدرسة. المستخدم حالياً {_human_size(used_bytes)}، "
        f"والملفات الجديدة {_human_size(incoming_bytes)}{replaced_text}، "
        f"والحد المتاح {_human_size(limit_bytes)}. "
    )
    if has_active_addon:
        return base + "يمكنك طلب زيادة المساحة من صفحة الاشتراك."

    # An expired add-on is the most likely reason a school that was uploading
    # fine yesterday is blocked today: the limit silently fell back to the free
    # tier while the stored data stayed put. Telling this manager to "delete old
    # files" points them away from the one action that restores their space.
    expired_addon = _expired_archive_addon(school)
    if expired_addon is not None:
        return base + (
            f"السبب أن إضافة الأرشفة انتهت بتاريخ {expired_addon.end_date}، فرجعت المساحة "
            "إلى الحد المجاني الأساسي دون حذف أي ملف. تجديد الإضافة من صفحة الاشتراك "
            "يعيد المساحة كاملة فوراً."
        )
    return base + "يرجى حذف ملفات قديمة أو ترقية باقة التخزين (إضافة الأرشفة) من صفحة الاشتراك."


def archive_available_years(*, school: School, teacher=None, school_wide: bool = False) -> list[str]:
    years: set[str] = set()

    reports_qs = Report.objects.filter(school=school)
    achievements_qs = TeacherAchievementFile.objects.filter(school=school)
    leadership_qs = SchoolLeadershipPortfolio.objects.filter(school=school)
    if not school_wide and teacher is not None:
        reports_qs = reports_qs.filter(teacher=teacher)
        achievements_qs = achievements_qs.filter(teacher=teacher)

    years.update(reports_qs.exclude(academic_year="").values_list("academic_year", flat=True).distinct())
    years.update(achievements_qs.values_list("academic_year", flat=True).distinct())
    if school_wide:
        years.update(leadership_qs.values_list("academic_year", flat=True).distinct())
        years.update(
            SchoolYearArchive.objects.filter(school=school)
            .exclude(academic_year="")
            .values_list("academic_year", flat=True)
            .distinct()
        )
        # التذاكر والتعاميم لا تحمل سنة دراسية في نموذجها. إذا كانت هي المحتوى
        # الوحيد، نستخدم سنة المدرسة الحالية كوعاء واضح للنسخة الإدارية.
        has_administrative_records = (
            Ticket.objects.filter(school=school).exists()
            or Notification.objects.filter(school=school).exists()
        )
        current_year = (getattr(school, "current_academic_year", "") or "").strip()
        if has_administrative_records and current_year:
            years.add(current_year)

    sorted_years = _clean_years(years)
    if reports_qs.filter(Q(academic_year="") | Q(academic_year__isnull=True)).exists():
        sorted_years.append(UNCLASSIFIED_YEAR)
    return sorted_years


def archive_payload(
    *,
    school: School,
    selected_year: str,
    teacher=None,
    school_wide: bool = False,
    search: str = "",
    teacher_id: int | None = None,
    category_id: int | None = None,
) -> dict:
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

    search = (search or "").strip()
    if search:
        reports_qs = reports_qs.filter(
            Q(title__icontains=search)
            | Q(teacher__name__icontains=search)
            | Q(teacher__phone__icontains=search)
        )
        achievements_qs = achievements_qs.filter(
            Q(teacher__name__icontains=search)
            | Q(teacher__phone__icontains=search)
        )
    if teacher_id:
        reports_qs = reports_qs.filter(teacher_id=teacher_id)
        achievements_qs = achievements_qs.filter(teacher_id=teacher_id)
    if category_id:
        reports_qs = reports_qs.filter(category_id=category_id)

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
