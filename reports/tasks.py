from celery import shared_task
from django.apps import apps
from .storage import _compress_image_file
import logging

logger = logging.getLogger(__name__)

@shared_task
def process_report_images(report_id):
    """
    Task to process images for a report (resizing, optimization).
    """
    Report = apps.get_model('reports', 'Report')
    try:
        report = Report.objects.get(pk=report_id)
    except Report.DoesNotExist:
        logger.error(f"Report {report_id} not found for image processing.")
        return False

    updated = False
    for field_name in ['image1', 'image2', 'image3', 'image4']:
        image_field = getattr(report, field_name)
        if image_field and hasattr(image_field, 'file'):
            try:
                # Process the image
                processed_file = _compress_image_file(image_field.file)
                if processed_file != image_field.file:
                    # Save the processed image back to the field
                    # We use save=False to avoid recursion if we trigger this from post_save
                    image_field.save(image_field.name, processed_file, save=False)
                    updated = True
            except Exception as e:
                logger.exception(f"Error processing {field_name} for report {report_id}: {e}")

    if updated:
        report.save(update_fields=['image1', 'image2', 'image3', 'image4'])
        logger.info(f"Successfully processed images for report {report_id}.")
    
    # Trigger PDF generation after image processing
    generate_report_pdf_task.delay(report_id)
    
    return True

@shared_task
def process_ticket_image(ticket_image_id):
    """
    Task to process a single ticket image.
    """
    TicketImage = apps.get_model('reports', 'TicketImage')
    try:
        ti = TicketImage.objects.get(pk=ticket_image_id)
    except TicketImage.DoesNotExist:
        return False

    if ti.image and hasattr(ti.image, 'file'):
        try:
            processed_file = _compress_image_file(ti.image.file)
            if processed_file != ti.image.file:
                ti.image.save(ti.image.name, processed_file, save=False)
                ti.save(update_fields=['image'])
                logger.info(f"Successfully processed ticket image {ticket_image_id}.")
        except Exception as e:
            logger.exception(f"Error processing ticket image {ticket_image_id}: {e}")
    
    return True

@shared_task
def generate_report_pdf_task(report_id):
    """
    Task to generate PDF for a report.
    """
    from django.template.loader import render_to_string
    from django.core.files.base import ContentFile
    from django.conf import settings
    from .utils import _resolve_department_for_category, _build_head_decision
    
    Report = apps.get_model('reports', 'Report')
    SchoolMembership = apps.get_model('reports', 'SchoolMembership')
    
    try:
        r = Report.objects.select_related('school', 'category', 'teacher').get(pk=report_id)
    except Report.DoesNotExist:
        logger.error(f"Report {report_id} not found for PDF generation.")
        return False

    # Update status to processing
    r.pdf_status = 'processing'
    r.save(update_fields=['pdf_status'])

    try:
        school_scope = r.school
        
        # Resolve department
        dept = _resolve_department_for_category(r.category)
        if dept is not None and school_scope is not None:
            try:
                dept_school = getattr(dept, "school", None)
                if dept_school is not None and dept_school != school_scope:
                    dept = None
            except Exception:
                dept = None
        
        head_decision = _build_head_decision(dept)

        # School Principal
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
            school_principal = getattr(settings, "SCHOOL_PRINCIPAL", "")

        # Print Color
        print_color = "#2563eb"
        if school_scope:
            color_val = getattr(school_scope, "print_primary_color", "") or ""
            if color_val:
                print_color = color_val
        
        if not print_color or print_color == "#2563eb":
            print_color = getattr(settings, "SCHOOL_PRINT_COLOR", "#2563eb")

        # School Name and Logo
        school_name = school_scope.name if school_scope else None
        school_logo = None
        if school_scope and school_scope.logo_file:
            school_logo = school_scope.logo_file.url

        # Render HTML
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
            }
        )

        from weasyprint import CSS, HTML
        css = CSS(string="@page { size: A4; margin: 14mm 12mm; }")
        
        # Generate PDF
        pdf_content = HTML(string=html).write_pdf(stylesheets=[css])

        # Save PDF to model
        filename = f"report-{r.pk}.pdf"
        r.pdf_file.save(filename, ContentFile(pdf_content), save=False)
        r.pdf_status = 'completed'
        r.save(update_fields=['pdf_file', 'pdf_status'])
        
        logger.info(f"Successfully generated PDF for report {report_id}.")
        return True

    except Exception as e:
        logger.exception(f"Error generating PDF for report {report_id}: {e}")
        r.pdf_status = 'failed'
        r.save(update_fields=['pdf_status'])
        return False

@shared_task
def send_notification_task(notification_id, teacher_ids=None):
    """
    Task to create NotificationRecipient objects in the background.
    """
    Notification = apps.get_model('reports', 'Notification')
    NotificationRecipient = apps.get_model('reports', 'NotificationRecipient')
    Teacher = apps.get_model('reports', 'Teacher')

    try:
        n = Notification.objects.get(pk=notification_id)
    except Notification.DoesNotExist:
        logger.error(f"Notification {notification_id} not found.")
        return False

    if teacher_ids:
        # Send to specific teachers
        teachers = Teacher.objects.filter(pk__in=teacher_ids, is_active=True)
    else:
        # Send to audience based on notification scope
        qs = Teacher.objects.filter(is_active=True)
        qs = qs.filter(
            school_memberships__is_active=True,
            school_memberships__school__is_active=True,
        ).distinct()

        if n.school:
            qs = qs.filter(school_memberships__school=n.school).distinct()
        
        teachers = qs

    # Create recipients in chunks to avoid memory issues with very large lists
    batch_size = 500
    teacher_list = list(teachers)
    for i in range(0, len(teacher_list), batch_size):
        batch = teacher_list[i:i + batch_size]
        NotificationRecipient.objects.bulk_create(
            [NotificationRecipient(notification=n, teacher=t) for t in batch],
            ignore_conflicts=True,
        )
    
    logger.info(f"Successfully sent notification {notification_id} to {len(teacher_list)} recipients.")
    return True
