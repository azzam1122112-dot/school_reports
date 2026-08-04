from __future__ import annotations

import json
import logging
from datetime import datetime, time as dt_time, timedelta
from urllib import error as urlerror
from urllib import request as urlrequest
from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.core.cache import cache as django_cache
from django.core.exceptions import ValidationError
from django.core.mail import EmailMultiAlternatives, send_mail
from django.core.validators import validate_email
from django.db.models import Count, Q
from django.template.loader import render_to_string
from django.utils import timezone

from .storage import _compress_image_file
from .telegram_alerts import TelegramDeliveryError, deliver_telegram_alert

logger = logging.getLogger(__name__)

from core import opmetrics


@shared_task(
    bind=True,
    ignore_result=True,
    autoretry_for=(TelegramDeliveryError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 4},
)
def send_telegram_alert_task(self, payload: dict[str, str]) -> str:
    """Deliver a safe operational alert without blocking the user request."""
    return deliver_telegram_alert(payload)


@shared_task(
    bind=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 5},
)
def delete_orphaned_storage_file_task(
    self,
    model_label: str,
    field_name: str,
    file_name: str,
) -> bool:
    """Retry deletion of an unreferenced local/R2 object after transient errors."""
    from .file_cleanup import delete_file_if_unreferenced

    return delete_file_if_unreferenced(model_label, field_name, file_name)


def _periodic_lock(lock_name: str, ttl: int = 600) -> bool:
    """Acquire a cache-based lock to prevent overlapping periodic tasks.

    Returns True if the lock was acquired (caller should proceed).
    Returns False if another instance already holds it (caller should skip).
    """
    return bool(django_cache.add(f"periodic_lock:{lock_name}", 1, timeout=ttl))


def _task_ctx(task_obj) -> tuple[str | None, int, str | None]:
    try:
        req = getattr(task_obj, "request", None)
        task_id = getattr(req, "id", None)
        retries = int(getattr(req, "retries", 0) or 0)
        headers = getattr(req, "headers", None) or {}
        trace_id = headers.get("trace_id") if hasattr(headers, "get") else None
        if not trace_id and task_id:
            trace_id = f"task-{task_id}"
        return task_id, retries, trace_id
    except Exception:
        return None, 0, None


def _email_delivery_configured() -> bool:
    """Return False when production SMTP is still at its placeholder config."""
    backend = (getattr(settings, "EMAIL_BACKEND", "") or "").lower()
    if "console" in backend or "locmem" in backend:
        return True
    if "smtp" not in backend:
        return True

    host = (getattr(settings, "EMAIL_HOST", "") or "").strip().lower()
    env = (getattr(settings, "ENV", "") or "").strip().lower()
    if env == "production" and host in {"", "localhost", "127.0.0.1"}:
        return False
    return True


@shared_task(bind=True, ignore_result=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, retry_kwargs={"max_retries": 3})
def cleanup_audit_logs_task(self, days: int | None = None, chunk_size: int = 2000) -> int:
    """Delete AuditLog rows older than N days.

    Note: archiving is intentionally handled via the management command because
    many production setups use ephemeral disks for workers.
    """
    AuditLog = apps.get_model("reports", "AuditLog")
    task_id, retries, trace_id = _task_ctx(self)
    logger.info(
        "Task start name=cleanup_audit_logs_task task_id=%s trace_id=%s retries=%s",
        task_id,
        trace_id,
        retries,
    )

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

    logger.info(
        "Task success name=cleanup_audit_logs_task task_id=%s trace_id=%s deleted=%s retention_days=%s",
        task_id,
        trace_id,
        deleted_total,
        retention_days,
    )
    opmetrics.increment("celery.task.success.cleanup_audit_logs_task")
    return deleted_total


@shared_task(bind=True, ignore_result=True)
def monitor_infrastructure_capacity_task(self) -> dict:
    """Warn before Redis or the session table runs the platform into trouble.

    Redis holds the cache, the sessions and the Celery queues on one instance.
    ``volatile-lru`` keeps a full instance from rejecting writes, but silent
    eviction still shows up as users being logged out and rate limits resetting,
    so the memory ratio needs to be visible *before* it gets there.
    """
    task_id, retries, trace_id = _task_ctx(self)
    report: dict = {"redis_used_percent": None, "expired_sessions": None, "alerts": []}

    def _threshold(name: str, default: int) -> int:
        # Deliberately not `value or default`: a configured 0 means "always
        # alert" and must survive, not fall back to the default.
        value = getattr(settings, name, default)
        try:
            return int(value)
        except (TypeError, ValueError):
            return default

    threshold = _threshold("REDIS_MEMORY_ALERT_PERCENT", 80)

    try:
        from django_redis import get_redis_connection

        info = get_redis_connection("default").info(section="memory")
        used = int(info.get("used_memory") or 0)
        limit = int(info.get("maxmemory") or 0)
        if limit > 0 and used > 0:
            percent = round((used / limit) * 100, 1)
            report["redis_used_percent"] = percent
            if percent >= threshold:
                message = (
                    f"Redis memory at {percent}% of its limit "
                    f"({round(used / (1024 * 1024), 1)} MB of {round(limit / (1024 * 1024), 1)} MB)."
                )
                report["alerts"].append(message)
                logger.error("Infrastructure capacity warning: %s", message)
                opmetrics.increment("infra.redis.memory_high")
    except Exception:
        # A missing Redis (local/dev) must not fail the periodic job.
        logger.debug("Redis memory probe unavailable", exc_info=True)

    try:
        Session = apps.get_model("sessions", "Session")
        expired = Session.objects.filter(expire_date__lt=timezone.now()).count()
        report["expired_sessions"] = expired
        if expired > _threshold("EXPIRED_SESSION_ALERT_THRESHOLD", 100_000):
            message = f"{expired} expired session rows are still pending cleanup."
            report["alerts"].append(message)
            logger.error("Infrastructure capacity warning: %s", message)
            opmetrics.increment("infra.sessions.backlog_high")
    except Exception:
        logger.debug("Session backlog probe failed", exc_info=True)

    if report["alerts"]:
        try:
            from .telegram_alerts import TelegramAlert, queue_telegram_alert

            queue_telegram_alert(
                TelegramAlert(
                    # Bucketed by day so a sustained condition alerts once daily
                    # rather than on every run.
                    event_key=f"infra:capacity:{timezone.localdate().isoformat()}",
                    category="support",
                    text="⚠️ <b>تنبيه سعة البنية التحتية</b>\n" + "\n".join(report["alerts"]),
                )
            )
        except Exception:
            logger.exception("Unable to queue infrastructure capacity alert")

    logger.info(
        "Task success name=monitor_infrastructure_capacity_task task_id=%s trace_id=%s retries=%s report=%s",
        task_id,
        trace_id,
        retries,
        report,
    )
    return report


@shared_task(bind=True, ignore_result=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, retry_kwargs={"max_retries": 3})
def cleanup_expired_sessions_task(self, chunk_size: int = 5000) -> int:
    """Delete expired ``django_session`` rows.

    Django never prunes this table on its own. Public traffic (registration
    forms, logins, checkout returns) keeps adding rows, so without this job the
    table grows without bound and every ``cached_db`` session miss gets slower.
    Deleting in chunks keeps the statement short enough to avoid long locks on
    a busy database.
    """
    Session = apps.get_model("sessions", "Session")
    task_id, retries, trace_id = _task_ctx(self)
    logger.info(
        "Task start name=cleanup_expired_sessions_task task_id=%s trace_id=%s retries=%s",
        task_id,
        trace_id,
        retries,
    )

    chunk_size = max(int(chunk_size), 100)
    expired = Session.objects.filter(expire_date__lt=timezone.now()).order_by("expire_date")

    deleted_total = 0
    while True:
        batch_keys = list(expired.values_list("session_key", flat=True)[:chunk_size])
        if not batch_keys:
            break
        deleted, _ = Session.objects.filter(session_key__in=batch_keys).delete()
        deleted_total += int(deleted)

    logger.info(
        "Task success name=cleanup_expired_sessions_task task_id=%s trace_id=%s deleted=%s",
        task_id,
        trace_id,
        deleted_total,
    )
    opmetrics.increment("celery.task.success.cleanup_expired_sessions_task")
    return deleted_total


@shared_task(bind=True, ignore_result=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, retry_kwargs={"max_retries": 3}, rate_limit="30/m")
def process_report_images(self, report_id: int) -> bool:
    """
    Task to process images for a report (compression/optimization).
    """
    task_id, retries, trace_id = _task_ctx(self)
    logger.info(
        "Task start name=process_report_images task_id=%s trace_id=%s retries=%s report_id=%s",
        task_id,
        trace_id,
        retries,
        report_id,
    )

    Report = apps.get_model("reports", "Report")
    try:
        report = Report.objects.get(pk=report_id)
    except Report.DoesNotExist:
        logger.error("Report %s not found for image processing.", report_id)
        opmetrics.increment("celery.task.failure.process_report_images")
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
            opmetrics.increment("celery.task.failure.process_report_images")

    if updated:
        report.save(update_fields=fields)
        logger.info(
            "Task success name=process_report_images task_id=%s trace_id=%s report_id=%s updated=%s",
            task_id,
            trace_id,
            report_id,
            updated,
        )
    opmetrics.increment("celery.task.success.process_report_images")

    return True


@shared_task(bind=True, ignore_result=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, retry_kwargs={"max_retries": 3}, rate_limit="30/m")
def process_ticket_image(self, ticket_image_id: int) -> bool:
    """
    Task to process a single ticket image (compression/optimization).
    """
    task_id, retries, trace_id = _task_ctx(self)
    logger.info(
        "Task start name=process_ticket_image task_id=%s trace_id=%s retries=%s ticket_image_id=%s",
        task_id,
        trace_id,
        retries,
        ticket_image_id,
    )

    TicketImage = apps.get_model("reports", "TicketImage")
    try:
        ticket_image = TicketImage.objects.get(pk=ticket_image_id)
    except TicketImage.DoesNotExist:
        logger.error("TicketImage %s not found for image processing.", ticket_image_id)
        opmetrics.increment("celery.task.failure.process_ticket_image")
        return False

    image_field = getattr(ticket_image, "image", None)
    if not image_field or not hasattr(image_field, "file"):
        opmetrics.increment("celery.task.failure.process_ticket_image")
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
            logger.info(
                "Task success name=process_ticket_image task_id=%s trace_id=%s ticket_image_id=%s updated=%s",
                task_id,
                trace_id,
                ticket_image_id,
                True,
            )

        opmetrics.increment("celery.task.success.process_ticket_image")

        return True

    except Exception as e:
        logger.exception(
            "Task failure name=process_ticket_image task_id=%s trace_id=%s ticket_image_id=%s error=%s",
            task_id,
            trace_id,
            ticket_image_id,
            e,
        )
        opmetrics.increment("celery.task.failure.process_ticket_image")
        return False


@shared_task(bind=True, ignore_result=True, autoretry_for=(Exception,), retry_backoff=True, retry_jitter=True, retry_kwargs={"max_retries": 3}, soft_time_limit=600, time_limit=900)
def send_notification_task(self, notification_id: int, teacher_ids=None) -> bool:
    """
    Task to create NotificationRecipient objects in the background.
    """
    task_id, retries, trace_id = _task_ctx(self)
    logger.info(
        "Task start name=send_notification_task task_id=%s trace_id=%s retries=%s notification_id=%s explicit_recipients=%s",
        task_id,
        trace_id,
        retries,
        notification_id,
        0 if not teacher_ids else len(teacher_ids),
    )

    Notification = apps.get_model("reports", "Notification")
    NotificationRecipient = apps.get_model("reports", "NotificationRecipient")
    Teacher = apps.get_model("reports", "Teacher")

    try:
        n = Notification.objects.get(pk=notification_id)
    except Notification.DoesNotExist:
        logger.error("Notification %s not found.", notification_id)
        opmetrics.increment("celery.task.failure.send_notification_task")
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
        _role_types = (
            ["teacher"]
            if bool(getattr(n, "requires_signature", False))
            else ["teacher", "report_viewer"]
        )
        if getattr(n, "school", None):
            qs = qs.filter(
                school_memberships__school=n.school,
                school_memberships__is_active=True,
                school_memberships__role_type__in=_role_types,
            ).exclude(
                is_platform_admin=True,
            ).distinct()
        else:
            qs = qs.filter(
                school_memberships__is_active=True,
                school_memberships__role_type__in=_role_types,
            ).exclude(
                is_platform_admin=True,
            ).distinct()

        teachers = qs

    batch_size = 500

    try:
        from .realtime_notifications import push_new_notification_to_teachers
    except Exception:
        push_new_notification_to_teachers = None

    # Stream teachers in chunks via values_list to avoid loading all objects
    # into memory.  At 50K schools × 25 teachers = 1.25M users, the old
    # `list(teachers)` would consume gigabytes of RAM.
    teacher_id_qs = teachers.values_list("id", flat=True)
    total_recipients = 0

    batch_ids: list[int] = []
    for tid in teacher_id_qs.iterator(chunk_size=batch_size):
        batch_ids.append(tid)
        if len(batch_ids) >= batch_size:
            NotificationRecipient.objects.bulk_create(
                [NotificationRecipient(notification=n, teacher_id=t) for t in batch_ids],
                ignore_conflicts=True,
            )
            if push_new_notification_to_teachers is not None:
                try:
                    push_new_notification_to_teachers(
                        notification=n,
                        teacher_ids=batch_ids,
                        trace_id=trace_id,
                    )
                except Exception:
                    pass
            total_recipients += len(batch_ids)
            batch_ids = []

    # Flush remaining
    if batch_ids:
        NotificationRecipient.objects.bulk_create(
            [NotificationRecipient(notification=n, teacher_id=t) for t in batch_ids],
            ignore_conflicts=True,
        )
        if push_new_notification_to_teachers is not None:
            try:
                push_new_notification_to_teachers(
                    notification=n,
                    teacher_ids=batch_ids,
                    trace_id=trace_id,
                )
            except Exception:
                pass
        total_recipients += len(batch_ids)

    logger.info(
        "Task success name=send_notification_task task_id=%s trace_id=%s notification_id=%s recipients=%s",
        task_id,
        trace_id,
        notification_id,
        total_recipients,
    )
    opmetrics.increment("celery.task.success.send_notification_task")
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


def _build_weekly_message(
    school_name: str,
    period_text: str,
    reports_count: int,
    open_tickets_count: int,
    closed_tickets_count: int,
    details_url: str,
) -> str:
    return (
        f"الملخص الأسبوعي - {school_name}\n\n"
        f"فترة التقرير: {period_text}\n"
        f"عدد التقارير: {int(reports_count)}\n"
        f"البلاغات المفتوحة: {int(open_tickets_count)}\n"
        f"البلاغات المغلقة: {int(closed_tickets_count)}\n\n"
        "عرض التفاصيل:\n"
        f"{details_url}"
    )


def _build_weekly_email_html(
    *,
    manager_name: str,
    school_name: str,
    period_text: str,
    reports_count: int,
    open_tickets_count: int,
    closed_tickets_count: int,
    details_url: str,
) -> str:
    site_url = (getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    static_url = (getattr(settings, "STATIC_URL", "/static/") or "/static/").strip()
    if not static_url.startswith("/"):
        static_url = f"/{static_url}"
    logo_url = f"{site_url}{static_url.rstrip('/')}/img/logo1.png" if site_url else ""

    return render_to_string(
        "reports/emails/weekly_manager_summary.html",
        {
            "platform_name": "منصة توثيق",
            "manager_name": manager_name or "إدارة المدرسة",
            "school_name": school_name,
            "period_text": period_text,
            "reports_count": int(reports_count),
            "open_tickets_count": int(open_tickets_count),
            "closed_tickets_count": int(closed_tickets_count),
            "details_url": details_url,
            "logo_url": logo_url,
        },
    )


def _weekly_summary_window(reference_dt=None):
    """Return the weekly reporting window: Sunday 00:00 -> Thursday 16:00 (exclusive)."""
    tz = timezone.get_current_timezone()
    now_local = timezone.localtime(reference_dt or timezone.now(), tz)

    # Python weekday: Monday=0 ... Sunday=6, Thursday=3
    days_since_thursday = (now_local.weekday() - 3) % 7
    thursday_date = (now_local - timedelta(days=days_since_thursday)).date()
    end_dt = timezone.make_aware(datetime.combine(thursday_date, dt_time(hour=16, minute=0)), tz)

    # If run before this week's Thursday 16:00, use the previous week's window.
    if now_local < end_dt:
        end_dt -= timedelta(days=7)

    start_date = (end_dt - timedelta(days=4)).date()  # Sunday
    start_dt = timezone.make_aware(datetime.combine(start_date, dt_time.min), tz)
    return start_dt, end_dt


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


@shared_task(ignore_result=True, soft_time_limit=60, time_limit=120)
def _daily_summary_for_school(school_id: int) -> dict:
    """Process weekly manager summary for a single school (fan-out subtask)."""
    School = apps.get_model("reports", "School")
    SchoolMembership = apps.get_model("reports", "SchoolMembership")
    Report = apps.get_model("reports", "Report")
    Ticket = apps.get_model("reports", "Ticket")

    inapp_enabled = bool(getattr(settings, "DAILY_MANAGER_REPORT_INAPP_ENABLED", True))
    email_enabled = bool(getattr(settings, "DAILY_MANAGER_REPORT_EMAIL_ENABLED", False))
    whatsapp_enabled = bool(getattr(settings, "DAILY_MANAGER_REPORT_WHATSAPP_ENABLED", False))
    from_email = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "no-reply@tawtheeq-ksa.com").strip()

    week_start, week_end = _weekly_summary_window()
    period_text = f"{week_start.strftime('%Y-%m-%d')} إلى {week_end.strftime('%Y-%m-%d %H:%M')}"
    open_ticket_statuses = ("open", "in_progress")
    closed_ticket_statuses = ("done", "rejected")

    result = {
        "school_id": school_id,
        "processed": False,
        "inapp_sent": 0,
        "emails_sent": 0,
        "whatsapp_sent": 0,
    }

    try:
        school = School.objects.filter(pk=school_id, is_active=True).only("id", "name").first()
    except Exception:
        school = None
    if school is None:
        return result

    manager_memberships = (
        SchoolMembership.objects.select_related("teacher")
        .filter(school=school, role_type="manager", is_active=True, teacher__is_active=True)
        .only(
            "teacher__id",
            "teacher__name",
            "teacher__phone",
            "teacher__email",
            "weekly_summary_email_enabled",
        )
    )
    manager_by_id: dict[int, dict[str, object]] = {}
    for membership in manager_memberships:
        manager = getattr(membership, "teacher", None)
        mid = int(getattr(manager, "id", 0) or 0)
        if manager is not None and mid and mid not in manager_by_id:
            manager_by_id[mid] = {
                "teacher": manager,
                "weekly_summary_email_enabled": bool(
                    getattr(membership, "weekly_summary_email_enabled", True)
                ),
            }

    managers = list(manager_by_id.values())
    if not managers:
        return result

    reports_count = Report.objects.filter(
        school=school, created_at__gte=week_start, created_at__lt=week_end,
    ).count()

    ticket_agg = Ticket.objects.filter(
        school=school,
        created_at__gte=week_start,
        created_at__lt=week_end,
    ).aggregate(
        open=Count("id", filter=Q(status__in=open_ticket_statuses)),
        closed=Count("id", filter=Q(status__in=closed_ticket_statuses)),
    )

    details_url = _build_school_details_url(school.id)
    message_text = _build_weekly_message(
        school_name=getattr(school, "name", "") or "المدرسة",
        period_text=period_text,
        reports_count=reports_count,
        open_tickets_count=ticket_agg["open"],
        closed_tickets_count=ticket_agg["closed"],
        details_url=details_url,
    )
    subject = f"الملخص الأسبوعي - {getattr(school, 'name', '') or 'المدرسة'}"

    manager_ids = list(manager_by_id.keys())
    inapp_recipient_ids: set[int] = set()
    if inapp_enabled and manager_ids:
        inapp_ok = _send_inapp_notification(
            school=school, manager_ids=manager_ids,
            subject=subject, message_text=message_text,
        )
        if inapp_ok:
            inapp_recipient_ids.update(manager_ids)
            result["inapp_sent"] += len(manager_ids)

    for manager_item in managers:
        manager = manager_item.get("teacher")
        mid = int(getattr(manager, "id", 0) or 0)
        if not mid:
            continue
        manager_email = (getattr(manager, "email", "") or "").strip()
        manager_phone = (getattr(manager, "phone", "") or "").strip()
        email_pref_enabled = bool(manager_item.get("weekly_summary_email_enabled", True))

        if email_enabled and email_pref_enabled and _is_valid_email(manager_email):
            try:
                email = EmailMultiAlternatives(
                    subject=subject,
                    body=message_text,
                    from_email=from_email,
                    to=[manager_email],
                )
                email.attach_alternative(
                    _build_weekly_email_html(
                        manager_name=(getattr(manager, "name", "") or "").strip(),
                        school_name=getattr(school, "name", "") or "المدرسة",
                        period_text=period_text,
                        reports_count=reports_count,
                        open_tickets_count=ticket_agg["open"],
                        closed_tickets_count=ticket_agg["closed"],
                        details_url=details_url,
                    ),
                    "text/html",
                )
                email.send(fail_silently=False)
                result["emails_sent"] += 1
            except Exception:
                logger.exception("Daily summary email failed school=%s manager=%s", school_id, mid)

        normalized_phone = _normalize_sa_whatsapp_phone(manager_phone)
        if whatsapp_enabled and normalized_phone:
            ok = _send_whatsapp_via_webhook(
                to_phone=normalized_phone, message_text=message_text,
                school_id=school.id, school_name=getattr(school, "name", "") or "",
                reports_count=reports_count,
                open_tickets_count=ticket_agg["open"],
                closed_tickets_count=ticket_agg["closed"],
                report_date_text=period_text,
            )
            if ok:
                result["whatsapp_sent"] += 1

    result["processed"] = True
    return result


@shared_task(ignore_result=True, soft_time_limit=300, time_limit=600)
def send_daily_manager_summary_task() -> dict:
    """
    Weekly summary dispatcher — fans out to one subtask per active school.

    Channels:
    - In-app notification (internal)
    - Email (manager email)
    - WhatsApp via configurable webhook (manager phone)
    """
    import time as _time
    _t0 = _time.monotonic()

    enabled = bool(getattr(settings, "DAILY_MANAGER_REPORT_ENABLED", True))

    if not _periodic_lock("daily_manager_summary", ttl=600):
        logger.info("Weekly manager summary task skipped: another instance is running.")
        return {"enabled": enabled, "skipped": "lock"}

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
        logger.info("Weekly manager summary task skipped: feature disabled.")
        return summary

    School = apps.get_model("reports", "School")

    school_ids = list(
        School.objects.filter(is_active=True).values_list("id", flat=True)
    )
    summary["schools_seen"] = len(school_ids)

    # Fan-out: dispatch one subtask per school to the periodic queue.
    # Each subtask runs independently with its own time limits.
    dispatched = 0
    for sid in school_ids:
        try:
            _daily_summary_for_school.delay(sid)
            dispatched += 1
        except Exception:
            logger.exception("Failed to dispatch weekly summary for school=%s", sid)

    summary["schools_processed"] = dispatched
    logger.info("Weekly manager summary dispatched %d/%d school subtasks", dispatched, len(school_ids))
    opmetrics.timing("celery.periodic.daily_manager_summary", (_time.monotonic() - _t0) * 1000)
    return summary


# ═══════════════════════════════════════════════════════════════
# مهمة 1: تذكير بقرب انتهاء الاشتراك
# ═══════════════════════════════════════════════════════════════
@shared_task(ignore_result=True, soft_time_limit=120, time_limit=300)
def check_subscription_expiry_task() -> dict:
    """
    تفحص الاشتراكات النشطة وترسل إشعارات عند اقتراب انتهائها.

    - تعمل يومياً عبر Celery Beat.
    - ترسل إشعار داخلي + إيميل (اختياري) لمدراء المدارس.
    - تتجنب التكرار بفحص عدم وجود إشعار مماثل خلال آخر 24 ساعة.
    """
    import time as _time
    _t0 = _time.monotonic()

    enabled = bool(getattr(settings, "SUBSCRIPTION_EXPIRY_REMINDER_ENABLED", True))

    if not _periodic_lock("check_subscription_expiry", ttl=300):
        logger.info("Subscription expiry task skipped: another instance is running.")
        return {"enabled": enabled, "skipped": "lock"}

    email_enabled = bool(getattr(settings, "SUBSCRIPTION_EXPIRY_REMINDER_EMAIL_ENABLED", False))
    reminder_days = getattr(settings, "SUBSCRIPTION_EXPIRY_REMINDER_DAYS", [14, 7, 3, 1])
    from_email = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "no-reply@tawtheeq-ksa.com").strip()

    summary = {
        "enabled": enabled,
        "subscriptions_checked": 0,
        "reminders_sent": 0,
        "emails_sent": 0,
        "skipped_duplicate": 0,
    }

    if not enabled:
        logger.info("Subscription expiry reminder task skipped: feature disabled.")
        return summary

    SchoolSubscription = apps.get_model("reports", "SchoolSubscription")
    SchoolMembership = apps.get_model("reports", "SchoolMembership")
    Notification = apps.get_model("reports", "Notification")
    NotificationRecipient = apps.get_model("reports", "NotificationRecipient")

    today = timezone.localdate()
    now = timezone.now()
    dedup_cutoff = now - timedelta(hours=24)

    subs = (
        SchoolSubscription.objects
        .filter(is_active=True)
        .select_related("school", "plan")
        .only("id", "school__id", "school__name", "plan__name", "end_date", "is_active", "canceled_at")
    )

    for sub in subs.iterator():
        if sub.canceled_at:
            continue
        summary["subscriptions_checked"] += 1

        days_left = (sub.end_date - today).days
        if days_left < 0 or days_left not in reminder_days:
            continue

        school = sub.school
        school_name = getattr(school, "name", "")

        # تجنب التكرار: لا نرسل نفس التنبيه مرتين خلال 24 ساعة
        dedup_title = f"⏰ اشتراك {school_name} ينتهي خلال {days_left}"
        already_sent = Notification.objects.filter(
            title=dedup_title,
            school=school,
            created_at__gte=dedup_cutoff,
        ).exists()
        if already_sent:
            summary["skipped_duplicate"] += 1
            continue

        # جلب مدراء المدرسة
        manager_ids = list(
            SchoolMembership.objects.filter(
                school=school,
                role_type="manager",
                is_active=True,
                teacher__is_active=True,
            ).values_list("teacher_id", flat=True)
        )
        if not manager_ids:
            continue

        if days_left == 1:
            message = f"⚠️ اشتراك مدرسة {school_name} (باقة {sub.plan.name}) ينتهي غداً!\nيرجى تجديد الاشتراك لتجنب توقف الخدمة."
        elif days_left <= 3:
            message = f"⚠️ اشتراك مدرسة {school_name} (باقة {sub.plan.name}) ينتهي خلال {days_left} أيام.\nيرجى تجديد الاشتراك قريباً."
        else:
            message = f"تنبيه: اشتراك مدرسة {school_name} (باقة {sub.plan.name}) ينتهي خلال {days_left} يوماً.\nيرجى التجديد في الوقت المناسب."

        # إشعار داخلي
        notification = Notification.objects.create(
            title=dedup_title,
            message=message,
            school=school,
            is_important=(days_left <= 3),
        )
        NotificationRecipient.objects.bulk_create(
            [NotificationRecipient(notification=notification, teacher_id=mid) for mid in manager_ids],
            ignore_conflicts=True,
        )

        try:
            from .realtime_notifications import push_new_notification_to_teachers
            push_new_notification_to_teachers(notification=notification, teacher_ids=manager_ids)
        except Exception:
            pass

        summary["reminders_sent"] += 1

        # إيميل (اختياري)
        if email_enabled:
            Teacher = apps.get_model("reports", "Teacher")
            managers_with_email = Teacher.objects.filter(
                id__in=manager_ids, is_active=True
            ).exclude(email="").only("id", "email")
            for mgr in managers_with_email:
                if _is_valid_email(mgr.email):
                    try:
                        send_mail(
                            subject=dedup_title,
                            message=message,
                            from_email=from_email,
                            recipient_list=[mgr.email],
                            fail_silently=False,
                        )
                        summary["emails_sent"] += 1
                    except Exception:
                        logger.exception("Subscription expiry email failed for teacher=%s", mgr.id)

    logger.info("Subscription expiry reminder result: %s", summary)
    opmetrics.timing("celery.periodic.check_subscription_expiry", (_time.monotonic() - _t0) * 1000)
    return summary


# ═══════════════════════════════════════════════════════════════
# مهمة 2: تذكير بالتعاميم غير الموقّعة قبل الموعد النهائي
# ═══════════════════════════════════════════════════════════════
@shared_task(ignore_result=True, soft_time_limit=120, time_limit=300)
def remind_unsigned_circulars_task() -> dict:
    """
    ترسل تذكيرات للمعلمين الذين لم يوقّعوا على تعاميم لها موعد نهائي قريب.

    - تعمل مرتين يومياً عبر Celery Beat.
    - تفحص التعاميم ذات `requires_signature=True` و `signature_deadline_at` قريب.
    - ترسل إشعار داخلي فقط للمعلمين الذين لم يوقّعوا بعد.
    - تتجنب التكرار بعدم التذكير أكثر من مرة واحدة لنفس المستلم لنفس التعميم خلال 12 ساعة.
    """
    import time as _time
    _t0 = _time.monotonic()

    enabled = bool(getattr(settings, "CIRCULAR_SIGNATURE_REMINDER_ENABLED", True))

    if not _periodic_lock("remind_unsigned_circulars", ttl=300):
        logger.info("Unsigned circular reminder task skipped: another instance is running.")
        return {"enabled": enabled, "skipped": "lock"}

    reminder_hours = getattr(settings, "CIRCULAR_SIGNATURE_REMINDER_HOURS", [48, 24])

    summary = {
        "enabled": enabled,
        "circulars_checked": 0,
        "reminders_sent": 0,
        "skipped_duplicate": 0,
    }

    if not enabled:
        logger.info("Circular signature reminder task skipped: feature disabled.")
        return summary

    Notification = apps.get_model("reports", "Notification")
    NotificationRecipient = apps.get_model("reports", "NotificationRecipient")

    now = timezone.now()
    dedup_cutoff = now - timedelta(hours=12)

    # أكبر عدد ساعات في القائمة يحدد نافذة البحث
    max_hours = max(reminder_hours) if reminder_hours else 48
    window_end = now + timedelta(hours=max_hours)

    # التعاميم التي تتطلب توقيع ولها موعد نهائي بين الآن ونهاية النافذة
    circulars = Notification.objects.filter(
        requires_signature=True,
        signature_deadline_at__gt=now,
        signature_deadline_at__lte=window_end,
    ).select_related("school").only(
        "id", "title", "signature_deadline_at", "school__id", "school__name"
    )

    for circular in circulars.iterator():
        hours_until_deadline = (circular.signature_deadline_at - now).total_seconds() / 3600
        summary["circulars_checked"] += 1

        # تحديد هل يقع الموعد ضمن إحدى نوافذ التذكير
        should_remind = False
        for h in sorted(reminder_hours):
            if hours_until_deadline <= h:
                should_remind = True
                break

        if not should_remind:
            continue

        # المعلمون الذين لم يوقّعوا بعد
        unsigned_recipients = NotificationRecipient.objects.filter(
            notification=circular,
            is_signed=False,
        ).values_list("teacher_id", flat=True)

        unsigned_ids = list(unsigned_recipients)
        if not unsigned_ids:
            continue

        # تجنب التكرار: لا نذكّر نفس المعلمين عن نفس التعميم خلال 12 ساعة
        dedup_title = f"🔔 تذكير بالتوقيع: {circular.title[:60]}"
        existing_reminder = Notification.objects.filter(
            title=dedup_title,
            school=circular.school,
            created_at__gte=dedup_cutoff,
        ).first()

        if existing_reminder:
            # تحقق هل المستلمون أنفسهم موجودون بالفعل
            already_reminded = set(
                NotificationRecipient.objects.filter(
                    notification=existing_reminder,
                    teacher_id__in=unsigned_ids,
                ).values_list("teacher_id", flat=True)
            )
            unsigned_ids = [uid for uid in unsigned_ids if uid not in already_reminded]
            if not unsigned_ids:
                summary["skipped_duplicate"] += 1
                continue

        hours_display = int(hours_until_deadline)
        if hours_display >= 24:
            time_text = f"{hours_display // 24} يوم"
        else:
            time_text = f"{hours_display} ساعة"

        message = (
            f"لم يتم توقيعك على التعميم \"{circular.title}\" بعد.\n"
            f"الموعد النهائي للتوقيع: خلال {time_text}.\n"
            "يرجى التوقيع في أقرب وقت."
        )

        reminder_notif = Notification.objects.create(
            title=dedup_title,
            message=message,
            school=circular.school,
            is_important=True,
        )
        NotificationRecipient.objects.bulk_create(
            [NotificationRecipient(notification=reminder_notif, teacher_id=uid) for uid in unsigned_ids],
            ignore_conflicts=True,
        )

        try:
            from .realtime_notifications import push_new_notification_to_teachers
            push_new_notification_to_teachers(notification=reminder_notif, teacher_ids=unsigned_ids)
        except Exception:
            pass

        summary["reminders_sent"] += len(unsigned_ids)

    logger.info("Unsigned circular reminder result: %s", summary)
    opmetrics.timing("celery.periodic.remind_unsigned_circulars", (_time.monotonic() - _t0) * 1000)
    return summary


# ═══════════════════════════════════════════════════════════════
# مهمة 3: إرسال إيميل تأكيد تغيير كلمة المرور
# ═══════════════════════════════════════════════════════════════
@shared_task(bind=True, ignore_result=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_password_change_email_task(self, teacher_id: int) -> bool:
    """
    ترسل إيميل تأكيد للمعلم بعد تغيير كلمة المرور بنجاح.

    - تُستدعى من view الملف الشخصي بعد تغيير كلمة المرور.
    - ترسل فقط إذا كان لدى المعلم بريد إلكتروني صالح.
    - أفضل ممارسة أمنية لتنبيه المستخدم بأي تغيير في حسابه.
    """
    task_id, retries, trace_id = _task_ctx(self)
    logger.info(
        "Task start name=send_password_change_email_task task_id=%s trace_id=%s retries=%s teacher_id=%s",
        task_id,
        trace_id,
        retries,
        teacher_id,
    )

    enabled = bool(getattr(settings, "PASSWORD_CHANGE_EMAIL_ENABLED", True))
    if not enabled:
        opmetrics.increment("celery.task.failure.send_password_change_email_task")
        return False
    if not _email_delivery_configured():
        logger.warning("Password change email skipped: production SMTP is not configured.")
        opmetrics.increment("celery.task.failure.send_password_change_email_task")
        return False

    Teacher = apps.get_model("reports", "Teacher")
    try:
        teacher = Teacher.objects.get(pk=teacher_id, is_active=True)
    except Teacher.DoesNotExist:
        logger.warning("Password change email: teacher %s not found.", teacher_id)
        opmetrics.increment("celery.task.failure.send_password_change_email_task")
        return False

    email = (getattr(teacher, "email", "") or "").strip()
    if not _is_valid_email(email):
        logger.info("Password change email: teacher %s has no valid email.", teacher_id)
        opmetrics.increment("celery.task.failure.send_password_change_email_task")
        return False

    teacher_name = (getattr(teacher, "name", "") or "").strip() or "المستخدم"
    from_email = (getattr(settings, "DEFAULT_FROM_EMAIL", "") or "no-reply@tawtheeq-ksa.com").strip()
    now_text = timezone.localtime(timezone.now()).strftime("%Y-%m-%d %H:%M")

    subject = "🔐 تم تغيير كلمة المرور - منصة توثيق"
    message = (
        f"مرحباً {teacher_name}،\n\n"
        f"تم تغيير كلمة المرور لحسابك في منصة توثيق بنجاح.\n"
        f"الوقت: {now_text}\n\n"
        "إذا لم تقم بهذا التغيير، يرجى التواصل مع إدارة المدرسة أو الدعم الفني فوراً.\n\n"
        "مع تحيات فريق منصة توثيق"
    )

    try:
        send_mail(
            subject=subject,
            message=message,
            from_email=from_email,
            recipient_list=[email],
            fail_silently=False,
        )
        logger.info(
            "Task success name=send_password_change_email_task task_id=%s trace_id=%s teacher_id=%s",
            task_id,
            trace_id,
            teacher_id,
        )
        opmetrics.increment("celery.task.success.send_password_change_email_task")
        return True
    except Exception:
        logger.exception(
            "Task failure name=send_password_change_email_task task_id=%s trace_id=%s teacher_id=%s retries=%s",
            task_id,
            trace_id,
            teacher_id,
            retries,
        )
        opmetrics.increment("celery.task.failure.send_password_change_email_task")
        raise  # auto-retry


@shared_task(bind=True, ignore_result=True)
def check_archive_addon_expiry_task(self) -> dict:
    """Warn managers before the yearly-archive add-on lapses.

    Storage is a separate product, so lapsing no longer freezes uploads — it
    only stops the school creating new yearly snapshots. Snapshots already saved
    stay downloadable either way, and the reminder says so.
    """
    task_id, retries, trace_id = _task_ctx(self)
    summary = {"addons_checked": 0, "reminders_sent": 0, "skipped_duplicate": 0}

    if not bool(getattr(settings, "ARCHIVE_ADDON_EXPIRY_REMINDER_ENABLED", True)):
        return summary

    if not _periodic_lock("check_archive_addon_expiry", ttl=300):
        logger.info("Archive add-on expiry task skipped: another instance is running.")
        return {**summary, "skipped": "lock"}

    reminder_days = getattr(settings, "SUBSCRIPTION_EXPIRY_REMINDER_DAYS", [14, 7, 3, 1])

    SchoolArchiveAddon = apps.get_model("reports", "SchoolArchiveAddon")
    SchoolMembership = apps.get_model("reports", "SchoolMembership")
    Notification = apps.get_model("reports", "Notification")
    NotificationRecipient = apps.get_model("reports", "NotificationRecipient")
    School = apps.get_model("reports", "School")

    today = timezone.localdate()
    dedup_cutoff = timezone.now() - timedelta(hours=24)

    addons = (
        SchoolArchiveAddon.objects.filter(is_enabled=True, end_date__isnull=False)
        .select_related("school")
        .only("id", "end_date", "storage_limit_gb", "school__id", "school__name")
    )

    for addon in addons.iterator():
        days_left = (addon.end_date - today).days
        if days_left < 0 or days_left not in reminder_days:
            continue

        summary["addons_checked"] += 1
        school = addon.school
        school_name = getattr(school, "name", "")

        dedup_title = f"🗄️ أرشفة {school_name} تنتهي خلال {days_left}"
        if Notification.objects.filter(
            title=dedup_title, school=school, created_at__gte=dedup_cutoff
        ).exists():
            summary["skipped_duplicate"] += 1
            continue

        manager_ids = list(
            SchoolMembership.objects.filter(
                school=school,
                role_type="manager",
                is_active=True,
                teacher__is_active=True,
            ).values_list("teacher_id", flat=True)
        )
        if not manager_ids:
            continue

        lines = [
            f"إضافة الأرشفة السنوية لمدرسة {school_name} تنتهي خلال {days_left} يوماً "
            f"(بتاريخ {addon.end_date}).",
            "بعد الانتهاء لن تتمكن المدرسة من إنشاء نسخة سنوية جديدة حتى التجديد.",
            # Say plainly what does NOT break, so nobody renews out of fear of
            # losing data or storage.
            "النسخ المحفوظة تبقى قابلة للتنزيل، ومساحة التخزين لا تتأثر إطلاقاً "
            "لأنها مستقلة عن الأرشفة السنوية.",
        ]

        notification = Notification.objects.create(
            title=dedup_title,
            message="\n".join(lines),
            school=school,
            is_important=(days_left <= 3),
        )
        NotificationRecipient.objects.bulk_create(
            [
                NotificationRecipient(notification=notification, teacher_id=mid)
                for mid in manager_ids
            ],
            ignore_conflicts=True,
        )
        try:
            from .realtime_notifications import push_new_notification_to_teachers

            push_new_notification_to_teachers(
                notification=notification, teacher_ids=manager_ids
            )
        except Exception:
            pass

        summary["reminders_sent"] += 1

    logger.info(
        "Task success name=check_archive_addon_expiry_task task_id=%s trace_id=%s retries=%s summary=%s",
        task_id,
        trace_id,
        retries,
        summary,
    )
    opmetrics.increment("celery.task.success.check_archive_addon_expiry_task")
    return summary


@shared_task(bind=True, ignore_result=True)
def check_storage_thresholds_task(self) -> dict:
    """Warn managers before their school runs out of storage.

    Discovering a full disk from a failed upload — mid-lesson, with a photo the
    teacher wanted to file — is the worst possible moment. This gives managers
    days of notice and names both ways out: raise the limit, or clear a year
    that a saved snapshot already preserves.
    """
    task_id, retries, trace_id = _task_ctx(self)
    summary = {"schools_checked": 0, "warnings_sent": 0, "skipped_duplicate": 0}

    if not bool(getattr(settings, "STORAGE_THRESHOLD_ALERTS_ENABLED", True)):
        return summary

    if not _periodic_lock("check_storage_thresholds", ttl=300):
        logger.info("Storage threshold task skipped: another instance is running.")
        return {**summary, "skipped": "lock"}

    from .services_archive import STORAGE_CRITICAL_PERCENT, school_storage_overview

    School = apps.get_model("reports", "School")
    SchoolMembership = apps.get_model("reports", "SchoolMembership")
    Notification = apps.get_model("reports", "Notification")
    NotificationRecipient = apps.get_model("reports", "NotificationRecipient")

    dedup_cutoff = timezone.now() - timedelta(days=3)

    schools = School.objects.filter(is_active=True).select_related(
        "subscription", "subscription__plan"
    )

    for school in schools.iterator():
        overview = school_storage_overview(school)
        if not overview["needs_attention"]:
            continue

        summary["schools_checked"] += 1
        percent = overview["usage_percent"]
        level = overview["warning_level"]

        # One title per level, so crossing from warning to critical still gets
        # through while a steady state does not repeat every day.
        dedup_title = f"💾 مساحة تخزين {school.name} ({level})"
        if Notification.objects.filter(
            title=dedup_title, school=school, created_at__gte=dedup_cutoff
        ).exists():
            summary["skipped_duplicate"] += 1
            continue

        manager_ids = list(
            SchoolMembership.objects.filter(
                school=school,
                role_type="manager",
                is_active=True,
                teacher__is_active=True,
            ).values_list("teacher_id", flat=True)
        )
        if not manager_ids:
            continue

        if level == "full":
            headline = (
                f"امتلأت مساحة تخزين {school.name} ({overview['used_label']} من "
                f"{overview['limit_label']}). رفع أي ملف جديد متوقف الآن."
            )
        else:
            headline = (
                f"مساحة تخزين {school.name} وصلت {percent}% "
                f"({overview['used_label']} من {overview['limit_label']})."
            )

        lines = [headline, "", "أمامك خياران:"]
        lines.append("• رفع حد التخزين من صفحة الاشتراك.")
        if overview["reclaimable_years"]:
            biggest = overview["reclaimable_years"][0]
            lines.append(
                f"• تفريغ {overview['reclaimable_label']} بحذف ملفات سنوات لها نسخة "
                f"سنوية محفوظة (أكبرها {biggest['label']} بحجم {biggest['size_label']}). "
                "النسخة المحفوظة تحتفظ بالسنة كاملة."
            )
        else:
            lines.append(
                "• أو حفظ نسخة سنوية لسنة سابقة ثم حذف ملفاتها الحية لتفريغ مساحتها."
            )

        notification = Notification.objects.create(
            title=dedup_title,
            message="\n".join(lines),
            school=school,
            is_important=(level in {"critical", "full"}),
        )
        NotificationRecipient.objects.bulk_create(
            [
                NotificationRecipient(notification=notification, teacher_id=mid)
                for mid in manager_ids
            ],
            ignore_conflicts=True,
        )
        try:
            from .realtime_notifications import push_new_notification_to_teachers

            push_new_notification_to_teachers(
                notification=notification, teacher_ids=manager_ids
            )
        except Exception:
            pass

        summary["warnings_sent"] += 1
        if percent >= STORAGE_CRITICAL_PERCENT:
            opmetrics.increment("storage.school.critical")

    logger.info(
        "Task success name=check_storage_thresholds_task task_id=%s trace_id=%s retries=%s summary=%s",
        task_id,
        trace_id,
        retries,
        summary,
    )
    opmetrics.increment("celery.task.success.check_storage_thresholds_task")
    return summary


@shared_task(bind=True, ignore_result=True)
def reconcile_pending_gateway_payments_task(self) -> dict:
    """Finish electronic payments the gateway never told us about.

    Activation used to hinge on the customer's browser returning to the site or
    on the gateway's callback reaching us. Either can fail, and nothing retried —
    leaving a school that had genuinely paid with no active subscription.
    """
    task_id, retries, trace_id = _task_ctx(self)

    if not bool(getattr(settings, "PAYMENT_RECONCILIATION_ENABLED", True)):
        return {"enabled": False}

    if not _periodic_lock("reconcile_gateway_payments", ttl=600):
        logger.info("Payment reconciliation skipped: another instance is running.")
        return {"skipped": "lock"}

    from .views.subscriptions import reconcile_pending_gateway_payments

    summary = reconcile_pending_gateway_payments()

    if summary.get("activated"):
        # A recovered payment means a customer-facing failure happened upstream;
        # make it visible instead of quietly papering over it.
        logger.warning(
            "Recovered %s payment(s) the gateway never confirmed to us: %s",
            summary["activated"],
            summary,
        )
        opmetrics.increment("payments.reconciled.activated", summary["activated"])
    if summary.get("failed"):
        opmetrics.increment("payments.reconciled.failed", summary["failed"])

    logger.info(
        "Task success name=reconcile_pending_gateway_payments_task task_id=%s trace_id=%s retries=%s summary=%s",
        task_id,
        trace_id,
        retries,
        summary,
    )
    opmetrics.increment("celery.task.success.reconcile_pending_gateway_payments_task")
    return summary
