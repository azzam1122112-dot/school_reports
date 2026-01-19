from __future__ import annotations

import logging
from datetime import timedelta
from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.utils import timezone

from .storage import _compress_image_file

logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def cleanup_audit_logs_task(self, days: int | None = None, chunk_size: int = 2000) -> int:
    """Delete AuditLog rows older than N days.

    Note: archiving is intentionally handled via the management command because
    many production setups use ephemeral disks for workers.
    """
    AuditLog = apps.get_model("reports", "AuditLog")

    retention_days = int(days) if days is not None else int(getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 30))
    retention_days = max(retention_days, 0)

    chunk_size = max(int(chunk_size), 100)
    cutoff = timezone.now() - timedelta(days=retention_days)

    qs = AuditLog.objects.filter(timestamp__lt=cutoff).order_by("pk")

    deleted_total = 0
    while True:
        batch_pks = list(qs.values_list("pk", flat=True)[:chunk_size])
        if not batch_pks:
            break
        deleted, _ = AuditLog.objects.filter(pk__in=batch_pks).delete()
        deleted_total += int(deleted)

    logger.info("AuditLog cleanup deleted %s rows older than %s days.", deleted_total, retention_days)
    return deleted_total


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def process_report_images(self, report_id: int) -> bool:
    """
    Task to process images for a report (compression/optimization).
    """
    Report = apps.get_model("reports", "Report")
    try:
        report = Report.objects.get(pk=report_id)
    except Report.DoesNotExist:
        logger.error("Report %s not found for image processing.", report_id)
        return False

    updated = False
    fields = ["image1", "image2", "image3", "image4"]

    for field_name in fields:
        image_field = getattr(report, field_name, None)
        if not image_field or not hasattr(image_field, "file"):
            continue

        try:
            processed_file = _compress_image_file(image_field.file)
            if not processed_file:
                continue

            # مقارنة آمنة: لو الحجم تغيّر نعتبره تحديث
            try:
                old_size = getattr(image_field.file, "size", None)
                new_size = getattr(processed_file, "size", None)
            except Exception:
                old_size, new_size = None, None

            if (new_size is not None and old_size is not None and new_size != old_size) or processed_file != image_field.file:
                image_field.save(image_field.name, processed_file, save=False)
                updated = True

        except Exception as e:
            logger.exception("Error processing %s for report %s: %s", field_name, report_id, e)

    if updated:
        report.save(update_fields=fields)
        logger.info("Successfully processed images for report %s.", report_id)

    return True


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def process_ticket_image(self, ticket_image_id: int) -> bool:
    """
    Task to process a single ticket image (compression/optimization).
    """
    TicketImage = apps.get_model("reports", "TicketImage")
    try:
        ticket_image = TicketImage.objects.get(pk=ticket_image_id)
    except TicketImage.DoesNotExist:
        logger.error("TicketImage %s not found for image processing.", ticket_image_id)
        return False

    image_field = getattr(ticket_image, "image", None)
    if not image_field or not hasattr(image_field, "file"):
        return False

    try:
        processed_file = _compress_image_file(image_field.file)
        if not processed_file:
            return True

        try:
            old_size = getattr(image_field.file, "size", None)
            new_size = getattr(processed_file, "size", None)
        except Exception:
            old_size, new_size = None, None

        if (new_size is not None and old_size is not None and new_size != old_size) or processed_file != image_field.file:
            image_field.save(image_field.name, processed_file, save=False)
            ticket_image.save(update_fields=["image"])
            logger.info("Successfully processed TicketImage %s.", ticket_image_id)

        return True

    except Exception as e:
        logger.exception("Error processing TicketImage %s: %s", ticket_image_id, e)
        return False


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_notification_task(self, notification_id: int, teacher_ids=None) -> bool:
    """
    Task to create NotificationRecipient objects in the background.
    """
    Notification = apps.get_model("reports", "Notification")
    NotificationRecipient = apps.get_model("reports", "NotificationRecipient")
    Teacher = apps.get_model("reports", "Teacher")

    try:
        n = Notification.objects.get(pk=notification_id)
    except Notification.DoesNotExist:
        logger.error("Notification %s not found.", notification_id)
        return False

    if teacher_ids:
        teachers = Teacher.objects.filter(pk__in=teacher_ids, is_active=True).only("id")
    else:
        qs = (
            Teacher.objects.filter(is_active=True)
            .filter(
                school_memberships__school__is_active=True,
            )
            .distinct()
            .only("id")
        )
        if getattr(n, "school", None):
            qs = qs.filter(
                school_memberships__school=n.school,
                school_memberships__role_type__in=(
                    ["teacher"]
                    if bool(getattr(n, "requires_signature", False))
                    else ["teacher", "report_viewer"]
                ),
            ).distinct()
        else:
            qs = qs.filter(
                school_memberships__role_type__in=(
                    ["teacher"]
                    if bool(getattr(n, "requires_signature", False))
                    else ["teacher", "report_viewer"]
                )
            ).distinct()

        teachers = qs

    batch_size = 500
    teacher_list = list(teachers)

    for i in range(0, len(teacher_list), batch_size):
        batch = teacher_list[i : i + batch_size]
        NotificationRecipient.objects.bulk_create(
            [NotificationRecipient(notification=n, teacher=t) for t in batch],
            ignore_conflicts=True,
        )

    logger.info("Successfully sent notification %s to %s recipients.", notification_id, len(teacher_list))
    return True
