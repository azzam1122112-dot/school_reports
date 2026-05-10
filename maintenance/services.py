from __future__ import annotations

import logging
from itertools import islice
from typing import Iterable, Iterator

from django.core.files.storage import default_storage
from django.db import transaction
from django.db.models import Q, QuerySet
from django.utils import timezone

from reports.models import (
    AchievementEvidenceImage,
    AchievementEvidenceReport,
    AuditLog,
    Notification,
    NotificationRecipient,
    Report,
    RequestTicket,
    School,
    ShareLink,
    TeacherAchievementFile,
    Ticket,
    TicketImage,
)

from .models import SchoolYearResetJob

logger = logging.getLogger(__name__)

INCLUDE_KEYS = ("reports", "tickets", "achievements", "notifications", "share_links")
CONFIRM_PHRASE = "RESET_SCHOOL_YEAR"


def _write_audit_log(job: SchoolYearResetJob | None, action: str, changes: dict) -> None:
    if job is None:
        return
    try:
        AuditLog.objects.create(
            teacher=job.created_by,
            action=action,
            model_name="SchoolYearResetJob",
            object_id=job.pk,
            object_repr=f"SchoolYearResetJob #{job.pk}",
            changes=changes,
        )
    except Exception:
        logger.exception("school year reset: failed to write audit log job_id=%s", job.pk)


def chunked(iterable: Iterable, size: int) -> Iterator[list]:
    iterator = iter(iterable)
    while True:
        chunk = list(islice(iterator, size))
        if not chunk:
            return
        yield chunk


def resolve_target_schools(
    *,
    all_schools: bool = False,
    school_ids: Iterable[int | str] | None = None,
    school_codes: Iterable[str] | None = None,
) -> QuerySet[School]:
    ids = [int(v) for v in (school_ids or []) if str(v).strip()]
    codes = [str(v).strip() for v in (school_codes or []) if str(v).strip()]

    if not all_schools and not ids and not codes:
        raise ValueError("حدد كل المدارس أو مرر school-id / school-code واحداً على الأقل.")

    if all_schools:
        qs = School.objects.all()
    else:
        query = Q()
        if ids:
            query |= Q(id__in=ids)
        if codes:
            query |= Q(code__in=codes)
        qs = School.objects.filter(query)

    return qs.order_by("name", "id")


def normalize_include_options(include_options: dict | None = None) -> dict[str, bool]:
    include_options = include_options or {}
    return {key: bool(include_options.get(key, True)) for key in INCLUDE_KEYS}


def options_from_job(job: SchoolYearResetJob) -> dict[str, bool]:
    return {
        "reports": bool(job.include_reports),
        "tickets": bool(job.include_tickets),
        "achievements": bool(job.include_achievements),
        "notifications": bool(job.include_notifications),
        "share_links": bool(job.include_share_links),
    }


def _school_ids(schools: Iterable[School] | QuerySet[School]) -> list[int]:
    if isinstance(schools, QuerySet):
        return list(schools.values_list("id", flat=True))
    return [int(s.id) for s in schools]


def _share_links_q(school_ids: list[int], include_options: dict[str, bool]) -> Q:
    query = Q(school_id__in=school_ids)
    if include_options.get("reports"):
        query |= Q(report__school_id__in=school_ids)
    if include_options.get("achievements"):
        query |= Q(achievement_file__school_id__in=school_ids)
    return query


def _file_values(qs: QuerySet, *fields: str) -> list[str]:
    keys: list[str] = []
    if not fields:
        return keys
    if len(fields) == 1:
        for value in qs.values_list(fields[0], flat=True):
            if value:
                keys.append(str(value))
        return keys
    for row in qs.values_list(*fields):
        for value in row:
            if value:
                keys.append(str(value))
    return keys


def collect_file_keys(schools: Iterable[School] | QuerySet[School], include_options: dict | None = None) -> list[str]:
    include_options = normalize_include_options(include_options)
    school_ids = _school_ids(schools)
    if not school_ids:
        return []

    keys: set[str] = set()

    if include_options["reports"]:
        keys.update(
            _file_values(
                Report.objects.filter(school_id__in=school_ids),
                "image1",
                "image2",
                "image3",
                "image4",
            )
        )

    if include_options["tickets"]:
        ticket_qs = Ticket.objects.filter(school_id__in=school_ids)
        keys.update(_file_values(ticket_qs, "attachment"))
        keys.update(_file_values(TicketImage.objects.filter(ticket__school_id__in=school_ids), "image"))

    if include_options["achievements"]:
        keys.update(_file_values(TeacherAchievementFile.objects.filter(school_id__in=school_ids), "pdf_file"))
        keys.update(
            _file_values(
                AchievementEvidenceImage.objects.filter(section__file__school_id__in=school_ids),
                "image",
            )
        )
        keys.update(
            _file_values(
                AchievementEvidenceReport.objects.filter(section__file__school_id__in=school_ids),
                "archived_image1",
                "archived_image2",
                "archived_image3",
                "archived_image4",
            )
        )

    if include_options["notifications"]:
        keys.update(_file_values(Notification.objects.filter(school_id__in=school_ids), "attachment"))

    return sorted(key for key in keys if key)


def collect_reset_summary(schools: Iterable[School] | QuerySet[School], include_options: dict | None = None) -> dict:
    include_options = normalize_include_options(include_options)
    school_ids = _school_ids(schools)
    file_keys = collect_file_keys(School.objects.filter(id__in=school_ids), include_options)

    summary = {
        "schools_count": len(school_ids),
        "school_ids": school_ids,
        "include_options": include_options,
        "reports_count": 0,
        "tickets_count": 0,
        "legacy_request_tickets_skipped": True,
        "ticket_images_count": 0,
        "achievements_count": 0,
        "achievement_evidence_images_count": 0,
        "achievement_evidence_reports_count": 0,
        "notifications_count": 0,
        "notification_recipients_count": 0,
        "share_links_count": 0,
        "file_keys_count": len(file_keys),
        "file_key_samples": file_keys[:20],
        "protected_data_note": "لن يتم حذف المدارس أو المستخدمين أو الأقسام أو أنواع التقارير أو الاشتراكات أو الصلاحيات أو الإعدادات.",
    }

    if not school_ids:
        return summary

    if include_options["reports"]:
        summary["reports_count"] = Report.objects.filter(school_id__in=school_ids).count()

    if include_options["tickets"]:
        summary["tickets_count"] = Ticket.objects.filter(school_id__in=school_ids).count()
        summary["ticket_images_count"] = TicketImage.objects.filter(ticket__school_id__in=school_ids).count()

    if include_options["achievements"]:
        summary["achievements_count"] = TeacherAchievementFile.objects.filter(school_id__in=school_ids).count()
        summary["achievement_evidence_images_count"] = AchievementEvidenceImage.objects.filter(
            section__file__school_id__in=school_ids
        ).count()
        summary["achievement_evidence_reports_count"] = AchievementEvidenceReport.objects.filter(
            section__file__school_id__in=school_ids
        ).count()

    if include_options["notifications"]:
        notification_qs = Notification.objects.filter(school_id__in=school_ids)
        summary["notifications_count"] = notification_qs.count()
        summary["notification_recipients_count"] = NotificationRecipient.objects.filter(
            notification__school_id__in=school_ids
        ).count()

    if include_options["share_links"] or include_options["reports"] or include_options["achievements"]:
        summary["share_links_count"] = ShareLink.objects.filter(_share_links_q(school_ids, include_options)).distinct().count()

    # RequestTicket has no school FK in the current schema; skipping it is intentional
    # to keep every deletion school-scoped.
    summary["legacy_request_tickets_total_unscoped"] = RequestTicket.objects.count()
    return summary


def build_file_manifest(file_keys: list[str]) -> dict:
    return {
        "total_files": len(file_keys),
        "file_keys": file_keys,
        "sample": file_keys[:20],
    }


def delete_storage_files(file_keys: Iterable[str], *, batch_size: int = 500) -> dict:
    unique_keys = sorted({str(key).strip() for key in file_keys if str(key).strip()})
    deleted = 0
    failed: list[dict[str, str]] = []

    for batch in chunked(unique_keys, max(1, int(batch_size or 500))):
        for key in batch:
            try:
                default_storage.delete(key)
                deleted += 1
            except Exception as exc:  # pragma: no cover - storage backend dependent
                logger.exception("school year reset: failed to delete storage key=%s", key)
                failed.append({"key": key, "error": str(exc)})

    return {
        "total_files": len(unique_keys),
        "deleted_files_count": deleted,
        "failed_files_count": len(failed),
        "failed_files": failed[:50],
    }


def _delete_qs(qs: QuerySet) -> dict:
    count, details = qs.delete()
    return {"total_deleted": count, "details": details}


def execute_school_year_reset(
    job_or_options: SchoolYearResetJob | dict,
    *,
    schools: Iterable[School] | QuerySet[School] | None = None,
    batch_size: int = 500,
) -> dict:
    if isinstance(job_or_options, SchoolYearResetJob):
        job = job_or_options
        include_options = options_from_job(job)
        delete_files = bool(job.delete_files)
        target_schools = job.schools.all()
    else:
        job = None
        include_options = normalize_include_options(job_or_options.get("include_options"))
        delete_files = bool(job_or_options.get("delete_files", False))
        target_schools = schools or School.objects.none()

    school_ids = _school_ids(target_schools)
    if not school_ids:
        raise ValueError("لا توجد مدارس مستهدفة للتنفيذ.")

    if job is not None:
        job.mark_running()

    file_keys = collect_file_keys(School.objects.filter(id__in=school_ids), include_options)
    manifest = build_file_manifest(file_keys)

    summary_before = collect_reset_summary(School.objects.filter(id__in=school_ids), include_options)
    execution: dict = {
        "school_ids": school_ids,
        "include_options": include_options,
        "summary_before": summary_before,
        "database_deletes": {},
        "files": {
            "total_files": len(file_keys),
            "deleted_files_count": 0,
            "failed_files_count": 0,
            "failed_files": [],
            "skipped_files": len(file_keys) if not delete_files else 0,
        },
        "protected_data_note": summary_before["protected_data_note"],
    }

    try:
        with transaction.atomic():
            if include_options["share_links"] or include_options["reports"] or include_options["achievements"]:
                share_link_ids = list(
                    ShareLink.objects.filter(_share_links_q(school_ids, include_options))
                    .values_list("pk", flat=True)
                    .distinct()
                )
                execution["database_deletes"]["share_links"] = _delete_qs(
                    ShareLink.objects.filter(pk__in=share_link_ids)
                )

            if include_options["notifications"]:
                execution["database_deletes"]["notifications"] = _delete_qs(
                    Notification.objects.filter(school_id__in=school_ids)
                )

            if include_options["tickets"]:
                execution["database_deletes"]["tickets"] = _delete_qs(
                    Ticket.objects.filter(school_id__in=school_ids)
                )
                execution["database_deletes"]["legacy_request_tickets"] = {
                    "total_deleted": 0,
                    "skipped": "RequestTicket has no school field; skipped to keep deletion school-scoped.",
                }

            if include_options["achievements"]:
                execution["database_deletes"]["achievements"] = _delete_qs(
                    TeacherAchievementFile.objects.filter(school_id__in=school_ids)
                )

            if include_options["reports"]:
                execution["database_deletes"]["reports"] = _delete_qs(
                    Report.objects.filter(school_id__in=school_ids)
                )

        if delete_files:
            execution["files"] = delete_storage_files(file_keys, batch_size=batch_size)

        failed_files = int(execution["files"].get("failed_files_count") or 0)
        status = SchoolYearResetJob.Status.PARTIAL if failed_files else SchoolYearResetJob.Status.COMPLETED
        execution["completed_at"] = timezone.now().isoformat()

        if job is not None:
            job.status = status
            job.execution_summary = execution
            job.file_manifest = manifest
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "execution_summary", "file_manifest", "finished_at"])
            _write_audit_log(
                job,
                AuditLog.Action.DELETE,
                {
                    "status": status,
                    "school_ids": school_ids,
                    "include_options": include_options,
                    "delete_files": delete_files,
                    "files": execution.get("files", {}),
                    "database_deletes": execution.get("database_deletes", {}),
                },
            )

        logger.warning("school year reset completed job_id=%s schools=%s status=%s", getattr(job, "id", None), school_ids, status)
        return execution
    except Exception as exc:
        logger.exception("school year reset failed job_id=%s schools=%s", getattr(job, "id", None), school_ids)
        if job is not None:
            job.status = SchoolYearResetJob.Status.FAILED
            job.error_message = str(exc)
            job.execution_summary = execution
            job.file_manifest = manifest
            job.finished_at = timezone.now()
            job.save(update_fields=["status", "error_message", "execution_summary", "file_manifest", "finished_at"])
            _write_audit_log(
                job,
                AuditLog.Action.UPDATE,
                {
                    "status": SchoolYearResetJob.Status.FAILED,
                    "school_ids": school_ids,
                    "error_message": str(exc),
                },
            )
        raise
