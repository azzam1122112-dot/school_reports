from __future__ import annotations

import logging
from celery import shared_task
from django.apps import apps
from django.conf import settings
from django.core.files.base import ContentFile

from .storage import _compress_image_file

logger = logging.getLogger(__name__)


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def process_report_images(self, report_id: int) -> bool:
    """
    Task to process images for a report (compression/optimization), then trigger PDF generation.
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

    # Trigger PDF generation after image processing
    try:
        from .utils import run_task_safe
        run_task_safe(generate_report_pdf_task, report_id, force_thread=True)
    except Exception as e:
        logger.exception("Failed to trigger PDF generation for report %s: %s", report_id, e)

    return True


@shared_task(bind=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def process_ticket_image(self, ticket_image_id: int) -> bool:
    """
    Task to process a single ticket image.
    """
    TicketImage = apps.get_model("reports", "TicketImage")
    try:
        ti = TicketImage.objects.get(pk=ticket_image_id)
    except TicketImage.DoesNotExist:
        return False

    if not getattr(ti, "image", None) or not hasattr(ti.image, "file"):
        return True

    try:
        processed_file = _compress_image_file(ti.image.file)
        if processed_file and processed_file != ti.image.file:
            ti.image.save(ti.image.name, processed_file, save=False)
            ti.save(update_fields=["image"])
            logger.info("Successfully processed ticket image %s.", ticket_image_id)
        return True
    except Exception as e:
        logger.exception("Error processing ticket image %s: %s", ticket_image_id, e)
        return False


@shared_task(bind=True)
def generate_report_pdf_task(self, report_id: int, return_bytes: bool = False):
    """
    Generate PDF for a report.

    Notes:
    - When called by Celery, keep `return_bytes=False` (default) and persist to storage.
    - When called directly from a Django view, `return_bytes=True` can be used to return PDF bytes
      for immediate download while still persisting the file.
    """
    from django.template.loader import render_to_string
    from .utils import _resolve_department_for_category, _build_head_decision

    Report = apps.get_model("reports", "Report")
    SchoolMembership = apps.get_model("reports", "SchoolMembership")

    try:
        r = Report.objects.select_related("school", "category", "teacher").get(pk=report_id)
    except Report.DoesNotExist:
        logger.error("Report %s not found for PDF generation.", report_id)
        return b"" if return_bytes else False

    # منع التكرار: لو جاري التوليد أو مكتمل وفيه ملف
    try:
        # If the caller asked for bytes (sync download), don't prematurely return empty bytes
        # just because status says processing/pending. We'll attempt generation below.
        if getattr(r, "pdf_status", "") in {"processing", "pending"} and not return_bytes:
            return True

        if getattr(r, "pdf_status", "") == "completed" and getattr(r, "pdf_file", None):
            if return_bytes:
                try:
                    r.pdf_file.open("rb")
                    data = r.pdf_file.read()
                    return data if data else b""
                except Exception:
                    pass
            return True
    except Exception:
        pass

    r.pdf_status = "processing"
    r.save(update_fields=["pdf_status"])

    try:
        school_scope = getattr(r, "school", None)

        dept = _resolve_department_for_category(r.category)
        if dept is not None and school_scope is not None:
            try:
                dept_school = getattr(dept, "school", None)
                if dept_school is not None and dept_school != school_scope:
                    dept = None
            except Exception:
                dept = None

        head_decision = _build_head_decision(dept)

        # Principal
        school_principal = ""
        if school_scope:
            principal_membership = (
                SchoolMembership.objects.select_related("teacher")
                .filter(
                    school=school_scope,
                    role_type=SchoolMembership.RoleType.MANAGER,
                    is_active=True,
                )
                .order_by("-id")
                .first()
            )
            if principal_membership and principal_membership.teacher:
                school_principal = getattr(principal_membership.teacher, "name", "") or ""

        if not school_principal:
            school_principal = getattr(settings, "SCHOOL_PRINCIPAL", "") or ""

        # Print Color
        print_color = "#2563eb"
        if school_scope:
            color_val = getattr(school_scope, "print_primary_color", "") or ""
            if color_val:
                print_color = color_val
        if not print_color or print_color == "#2563eb":
            print_color = getattr(settings, "SCHOOL_PRINT_COLOR", "#2563eb")

        # School Name & Logo
        school_name = getattr(school_scope, "name", None) if school_scope else None
        school_logo = None
        if school_scope and getattr(school_scope, "logo_file", None):
            try:
                school_logo = school_scope.logo_file.url
            except Exception:
                school_logo = None

        html = render_to_string(
            "reports/report_print.html",
            {
                "r": r,
                "for_pdf": True,
                "head_decision": head_decision,
                "SCHOOL_PRINCIPAL": school_principal,
                "PRINT_PRIMARY_COLOR": print_color,
                "SCHOOL_NAME": school_name,
                "SCHOOL_LOGO_URL": school_logo,
            },
        )

        try:
            from weasyprint import CSS, HTML
        except (ImportError, OSError) as e:
            logger.error("WeasyPrint import failed (missing dependencies?): %s", e)
            r.pdf_status = "failed"
            r.save(update_fields=["pdf_status"])
            return b"" if return_bytes else False

        # ✅ مهم على Render: base_url للستايل والصور
        base_url = (
            getattr(settings, "WEASYPRINT_BASE_URL", "") or
            getattr(settings, "SITE_URL", "") or
            ""
        ).strip() or None

        css = CSS(string="@page { size: A4; margin: 14mm 12mm; }")
        pdf_content: bytes = HTML(string=html, base_url=base_url).write_pdf(stylesheets=[css])

        filename = f"report-{r.pk}.pdf"
        r.pdf_file.save(filename, ContentFile(pdf_content), save=False)
        r.pdf_status = "completed"
        r.save(update_fields=["pdf_file", "pdf_status"])

        logger.info("Successfully generated PDF for report %s.", report_id)

        return pdf_content if return_bytes else True

    except Exception as e:
        logger.exception("Error generating PDF for report %s: %s", report_id, e)
        r.pdf_status = "failed"
        r.save(update_fields=["pdf_status"])
        return b"" if return_bytes else False


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
                school_memberships__is_active=True,
                school_memberships__school__is_active=True,
            )
            .distinct()
            .only("id")
        )
        if getattr(n, "school", None):
            qs = qs.filter(school_memberships__school=n.school).distinct()

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
