from __future__ import annotations

import json
import logging
from datetime import datetime, time as dt_time, timedelta
from urllib import error as urlerror
from urllib import request as urlrequest
from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.mail import send_mail
from django.core.validators import validate_email
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

    try:
        from .realtime_notifications import push_new_notification_to_teachers
    except Exception:
        push_new_notification_to_teachers = None

    for i in range(0, len(teacher_list), batch_size):
        batch = teacher_list[i : i + batch_size]
        NotificationRecipient.objects.bulk_create(
            [NotificationRecipient(notification=n, teacher=t) for t in batch],
            ignore_conflicts=True,
        )

        # WS push (bulk_create bypasses signals)
        if push_new_notification_to_teachers is not None:
            try:
                push_new_notification_to_teachers(
                    notification=n,
                    teacher_ids=[getattr(t, "id", None) for t in batch if getattr(t, "id", None)],
                )
            except Exception:
                pass

    logger.info("Successfully sent notification %s to %s recipients.", notification_id, len(teacher_list))
    return True


def _is_valid_email(value: str) -> bool:
    email = (value or "").strip()
    if not email:
        return False
    try:
        validate_email(email)
        return True
    except ValidationError:
        return False


def _normalize_sa_whatsapp_phone(value: str) -> str:
    """
    Normalize Saudi phone formats to a WhatsApp-compatible format:
    - 05XXXXXXXX -> 9665XXXXXXXX
    - 5XXXXXXXX  -> 9665XXXXXXXX
    - 9665XXXXXXX -> 9665XXXXXXX
    Returns empty string when normalization is not possible.
    """
    digits = "".join(ch for ch in str(value or "") if ch.isdigit())
    if not digits:
        return ""

    if digits.startswith("966") and len(digits) >= 12:
        return digits
    if digits.startswith("05") and len(digits) == 10:
        return f"966{digits[1:]}"
    if digits.startswith("5") and len(digits) == 9:
        return f"966{digits}"
    return ""


def _build_school_details_url(school_id: int) -> str:
    base = (getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    path = f"/staff/schools/{int(school_id)}/profile/"
    if base:
        return f"{base}{path}"
    return path


def _build_daily_message(
    school_name: str,
    report_date_text: str,
    reports_count: int,
    open_tickets_count: int,
    closed_tickets_count: int,
    details_url: str,
) -> str:
    return (
        f"تقرير اليوم - {school_name}\n\n"
        f"تاريخ التقرير: {report_date_text}\n"
        f"عدد التقارير: {int(reports_count)}\n"
        f"البلاغات المفتوحة: {int(open_tickets_count)}\n"
        f"البلاغات المغلقة: {int(closed_tickets_count)}\n\n"
        "عرض التفاصيل:\n"
        f"{details_url}"
    )


def _post_json(url: str, payload: dict, timeout_seconds: float = 10.0, token: str = "") -> bool:
    if not url:
        return False

    headers = {"Content-Type": "application/json; charset=utf-8"}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urlrequest.Request(url, data=data, headers=headers, method="POST")
    try:
        with urlrequest.urlopen(req, timeout=float(timeout_seconds)) as resp:
            status = int(getattr(resp, "status", 0) or 0)
            return 200 <= status < 300
    except (urlerror.URLError, urlerror.HTTPError, TimeoutError):
        return False


def _send_whatsapp_via_webhook(
    *,
    to_phone: str,
    message_text: str,
    school_id: int,
    school_name: str,
    reports_count: int,
    open_tickets_count: int,
    closed_tickets_count: int,
    report_date_text: str,
) -> bool:
    webhook_url = (getattr(settings, "DAILY_MANAGER_REPORT_WHATSAPP_WEBHOOK_URL", "") or "").strip()
    webhook_token = (getattr(settings, "DAILY_MANAGER_REPORT_WHATSAPP_WEBHOOK_TOKEN", "") or "").strip()
    timeout_seconds = float(getattr(settings, "DAILY_MANAGER_REPORT_WHATSAPP_TIMEOUT_SECONDS", 10.0) or 10.0)

    if not webhook_url:
        return False

    payload = {
        "channel": "whatsapp",
        "to": to_phone,
        "message": message_text,
        "school_id": int(school_id),
        "school_name": school_name,
        "report_date": report_date_text,
        "metrics": {
            "reports_count": int(reports_count),
            "open_tickets_count": int(open_tickets_count),
            "closed_tickets_count": int(closed_tickets_count),
        },
    }
    return _post_json(webhook_url, payload, timeout_seconds=timeout_seconds, token=webhook_token)


def _send_inapp_notification(
    *,
    school,
    manager_ids: list[int],
    subject: str,
    message_text: str,
) -> bool:
    if not manager_ids:
        return False

    Notification = apps.get_model("reports", "Notification")
    NotificationRecipient = apps.get_model("reports", "NotificationRecipient")

    try:
        notification = Notification.objects.create(
            title=subject,
            message=message_text,
            school=school,
            is_important=True,
        )
        NotificationRecipient.objects.bulk_create(
            [
                NotificationRecipient(
                    notification=notification,
                    teacher_id=manager_id,
                )
                for manager_id in manager_ids
            ],
            ignore_conflicts=True,
        )
    except Exception:
        logger.exception(
            "Daily manager in-app notification create failed for school=%s",
            getattr(school, "id", None),
        )
        return False

    try:
        from .realtime_notifications import push_new_notification_to_teachers
    except Exception:
        push_new_notification_to_teachers = None

    if push_new_notification_to_teachers is not None:
        try:
            push_new_notification_to_teachers(
                notification=notification,
                teacher_ids=manager_ids,
            )
        except Exception:
            pass

    return True


@shared_task
def send_daily_manager_summary_task() -> dict:
    """
    Daily summary for each active school manager.

    Channels:
    - In-app notification (internal)
    - Email (manager email)
    - WhatsApp via configurable webhook (manager phone)
    """
    enabled = bool(getattr(settings, "DAILY_MANAGER_REPORT_ENABLED", True))
    inapp_enabled = bool(getattr(settings, "DAILY_MANAGER_REPORT_INAPP_ENABLED", True))
    email_enabled = bool(getattr(settings, "DAILY_MANAGER_REPORT_EMAIL_ENABLED", False))
    whatsapp_enabled = bool(getattr(settings, "DAILY_MANAGER_REPORT_WHATSAPP_ENABLED", False))
    from_email = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "no-reply@tawtheeq-ksa.com").strip()

    summary = {
        "enabled": enabled,
        "schools_seen": 0,
        "schools_processed": 0,
        "schools_without_manager": 0,
        "inapp_sent": 0,
        "inapp_failures": 0,
        "emails_sent": 0,
        "email_failures": 0,
        "whatsapp_sent": 0,
        "whatsapp_failures": 0,
        "managers_missing_channels": 0,
    }

    if not enabled:
        logger.info("Daily manager summary task skipped: feature disabled.")
        return summary

    School = apps.get_model("reports", "School")
    SchoolMembership = apps.get_model("reports", "SchoolMembership")
    Report = apps.get_model("reports", "Report")
    Ticket = apps.get_model("reports", "Ticket")

    today = timezone.localdate()
    tz = timezone.get_current_timezone()
    day_start = timezone.make_aware(datetime.combine(today, dt_time.min), tz)
    day_end = day_start + timedelta(days=1)
    report_date_text = today.strftime("%Y-%m-%d")

    open_ticket_statuses = ("open", "in_progress")
    closed_ticket_statuses = ("done", "rejected")

    schools = School.objects.filter(is_active=True).only("id", "name")
    summary["schools_seen"] = schools.count()

    for school in schools:
        manager_memberships = (
            SchoolMembership.objects.select_related("teacher")
            .filter(
                school=school,
                role_type="manager",
                is_active=True,
                teacher__is_active=True,
            )
            .only("teacher__id", "teacher__name", "teacher__phone", "teacher__email")
        )
        manager_by_id: dict[int, object] = {}
        for membership in manager_memberships:
            manager = getattr(membership, "teacher", None)
            manager_id = int(getattr(manager, "id", 0) or 0)
            if manager is not None and manager_id and manager_id not in manager_by_id:
                manager_by_id[manager_id] = manager

        managers = list(manager_by_id.values())
        if not managers:
            summary["schools_without_manager"] += 1
            continue

        reports_count = Report.objects.filter(
            school=school,
            created_at__gte=day_start,
            created_at__lt=day_end,
        ).count()

        school_tickets = Ticket.objects.filter(school=school)
        open_tickets_count = school_tickets.filter(status__in=open_ticket_statuses).count()
        closed_tickets_count = school_tickets.filter(status__in=closed_ticket_statuses).count()

        details_url = _build_school_details_url(getattr(school, "id"))
        message_text = _build_daily_message(
            school_name=getattr(school, "name", "") or "المدرسة",
            report_date_text=report_date_text,
            reports_count=reports_count,
            open_tickets_count=open_tickets_count,
            closed_tickets_count=closed_tickets_count,
            details_url=details_url,
        )
        subject = f"تقرير اليوم - {getattr(school, 'name', '') or 'المدرسة'}"

        manager_ids = list(manager_by_id.keys())
        inapp_recipient_ids: set[int] = set()
        if inapp_enabled and manager_ids:
            inapp_ok = _send_inapp_notification(
                school=school,
                manager_ids=manager_ids,
                subject=subject,
                message_text=message_text,
            )
            if inapp_ok:
                inapp_recipient_ids.update(manager_ids)
                summary["inapp_sent"] += len(manager_ids)
            else:
                summary["inapp_failures"] += len(manager_ids)
                logger.error(
                    "Daily manager in-app notification failed for school=%s",
                    getattr(school, "id", None),
                )

        for manager in managers:
            manager_id = int(getattr(manager, "id", 0) or 0)
            if not manager_id:
                continue

            sent_any_channel = manager_id in inapp_recipient_ids
            manager_email = (getattr(manager, "email", "") or "").strip()
            manager_phone = (getattr(manager, "phone", "") or "").strip()

            if email_enabled and _is_valid_email(manager_email):
                try:
                    send_mail(
                        subject=subject,
                        message=message_text,
                        from_email=from_email,
                        recipient_list=[manager_email],
                        fail_silently=False,
                    )
                    summary["emails_sent"] += 1
                    sent_any_channel = True
                except Exception:
                    summary["email_failures"] += 1
                    logger.exception(
                        "Daily manager report email failed for manager=%s school=%s",
                        manager_id,
                        getattr(school, "id", None),
                    )

            normalized_phone = _normalize_sa_whatsapp_phone(manager_phone)
            if whatsapp_enabled and normalized_phone:
                ok = _send_whatsapp_via_webhook(
                    to_phone=normalized_phone,
                    message_text=message_text,
                    school_id=getattr(school, "id"),
                    school_name=getattr(school, "name", "") or "",
                    reports_count=reports_count,
                    open_tickets_count=open_tickets_count,
                    closed_tickets_count=closed_tickets_count,
                    report_date_text=report_date_text,
                )
                if ok:
                    summary["whatsapp_sent"] += 1
                    sent_any_channel = True
                else:
                    summary["whatsapp_failures"] += 1

            if not sent_any_channel:
                summary["managers_missing_channels"] += 1

        summary["schools_processed"] += 1

    logger.info("Daily manager summary result: %s", summary)
    return summary
