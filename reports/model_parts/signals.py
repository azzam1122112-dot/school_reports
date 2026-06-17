from __future__ import annotations

from .base import *
from .audit import AuditLog
from .achievements import AchievementEvidenceImage, AchievementEvidenceReport, TeacherAchievementFile
from .reports import Report
from .schools import DepartmentMembership, SchoolMembership, Teacher
from .tickets import Ticket
from .notifications import TicketImage

@receiver(post_save, sender=Report)
def trigger_report_background_tasks(sender, instance, created, **kwargs):
    """
    عند إنشاء تقرير جديد أو تحديثه، نقوم بجدولة المهام في الخلفية وتحديث الكاش.
    """
    from django.core.cache import cache
    if instance.school_id:
        cache.delete(f"admin_stats_{instance.school_id}")
    cache.delete("platform_admin_stats")

    from ..tasks import process_report_images
    from ..utils import run_task_safe

    # 1. معالجة الصور (إذا وجدت)
    has_images = any([instance.image1, instance.image2, instance.image3, instance.image4])
    
    if has_images:
        # معالجة الصور فقط (لا نقوم بتوليد PDF)
        run_task_safe(process_report_images, instance.pk)
    # إذا لم توجد صور: لا يوجد أي مهام مطلوبة هنا
    _sync_archive_usage_after_commit(getattr(instance, "school", None))


def _sync_archive_usage_after_commit(school):
    if school is None:
        return

    def _sync():
        try:
            from ..services_archive import sync_school_archive_storage_usage

            sync_school_archive_storage_usage(school)
        except Exception:
            pass

    try:
        transaction.on_commit(_sync)
    except Exception:
        _sync()


def _achievement_school(instance):
    try:
        return instance.section.file.school
    except Exception:
        return None


@receiver(models.signals.post_delete, sender=Report)
def sync_archive_usage_after_report_delete(sender, instance, **kwargs):
    _sync_archive_usage_after_commit(getattr(instance, "school", None))


@receiver(post_save, sender=TeacherAchievementFile)
def sync_archive_usage_after_achievement_file_save(sender, instance, **kwargs):
    _sync_archive_usage_after_commit(getattr(instance, "school", None))


@receiver(models.signals.post_delete, sender=TeacherAchievementFile)
def sync_archive_usage_after_achievement_file_delete(sender, instance, **kwargs):
    _sync_archive_usage_after_commit(getattr(instance, "school", None))


@receiver(post_save, sender=AchievementEvidenceImage)
def sync_archive_usage_after_evidence_image_save(sender, instance, **kwargs):
    _sync_archive_usage_after_commit(_achievement_school(instance))


@receiver(models.signals.post_delete, sender=AchievementEvidenceImage)
def sync_archive_usage_after_evidence_image_delete(sender, instance, **kwargs):
    _sync_archive_usage_after_commit(_achievement_school(instance))


@receiver(post_save, sender=AchievementEvidenceReport)
def sync_archive_usage_after_evidence_report_save(sender, instance, **kwargs):
    _sync_archive_usage_after_commit(_achievement_school(instance))


@receiver(models.signals.post_delete, sender=AchievementEvidenceReport)
def sync_archive_usage_after_evidence_report_delete(sender, instance, **kwargs):
    _sync_archive_usage_after_commit(_achievement_school(instance))


@receiver(post_save, sender=Ticket)
def trigger_ticket_notifications(sender, instance, created, **kwargs):
    """
    عند إنشاء تذكرة جديدة، نقوم بإرسال إشعارات للمسؤولين المعنيين وتحديث الكاش.
    """
    from django.core.cache import cache
    if instance.school_id:
        cache.delete(f"admin_stats_{instance.school_id}")
    cache.delete("platform_admin_stats")

    if not created:
        return

    from ..utils import create_system_notification

    title = f"تذكرة جديدة: {instance.title}"
    message = f"تم إنشاء طلب جديد بواسطة {instance.creator.name}. الحالة: {instance.get_status_display()}"

    if instance.is_platform:
        # تذكرة منصة: إشعار للسوبر يوزر
        superusers = Teacher.objects.filter(is_superuser=True).values_list('id', flat=True)
        if superusers:
            create_system_notification(
                title=f"🆘 دعم فني: {instance.title}",
                message=message,
                teacher_ids=list(superusers),
                is_important=True
            )
    else:
        # تذكرة مدرسة: إشعار للمدير ومسؤول القسم
        recipients = set()
        
        # 1. مدير المدرسة
        if instance.school:
            managers = SchoolMembership.objects.filter(
                school=instance.school,
                role_type=SchoolMembership.RoleType.MANAGER,
                is_active=True
            ).values_list('teacher_id', flat=True)
            recipients.update(managers)

        # 2. مسؤول القسم (إذا تم تحديد قسم)
        if instance.department:
            officers = DepartmentMembership.objects.filter(
                department=instance.department,
                role_type=DepartmentMembership.OFFICER
            ).values_list('teacher_id', flat=True)
            recipients.update(officers)

        if recipients:
            create_system_notification(
                title=title,
                message=message,
                school=instance.school,
                teacher_ids=list(recipients)
            )


@receiver(post_save, sender=TicketImage)
def trigger_ticket_image_processing(sender, instance, created, **kwargs):
    """
    عند رفع صورة تذكرة، نقوم بجدولة معالجتها في الخلفية.
    """
    from ..tasks import process_ticket_image
    if instance.image:
        try:
            _pk = instance.pk
            def _enqueue_ticket_image():
                try:
                    from core.trace_context import get_trace_id as _get_trace_id
                    _tid = _get_trace_id()
                except Exception:
                    _tid = None
                if not _tid:
                    import secrets
                    _tid = secrets.token_hex(8)
                process_ticket_image.apply_async(args=[_pk], headers={"trace_id": _tid})
            transaction.on_commit(_enqueue_ticket_image)
        except Exception:
            pass


# =========================
# سجل العمليات (Audit Logs)
@receiver(post_save)
def audit_log_save(sender, instance, created, **kwargs):
    from ..middleware import is_audit_logging_suppressed

    if is_audit_logging_suppressed():
        return

    # قائمة النماذج التي نريد مراقبتها
    monitored_models = ["Report", "Teacher", "School", "Department", "Ticket", "SchoolSubscription"]
    if sender.__name__ not in monitored_models:
        return

    from ..middleware import get_current_request
    request = get_current_request()
    if not request or not request.user.is_authenticated:
        return

    action = AuditLog.Action.CREATE if created else AuditLog.Action.UPDATE
    
    # محاولة تحديد المدرسة
    school = getattr(instance, "school", None)
    if not school and sender.__name__ == "School":
        school = instance

    # تسجيل التغييرات (بشكل مبسط)
    changes = {}
    if not created:
        # في حالة التعديل، يمكننا لاحقاً إضافة منطق لمقارنة القيم القديمة والجديدة
        pass

    AuditLog.objects.create(
        school=school,
        teacher=request.user,
        action=action,
        model_name=sender.__name__,
        object_id=instance.pk if hasattr(instance, "pk") else None,
        object_repr=str(instance)[:255],
        changes=changes,
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500]
    )


@receiver(models.signals.post_delete)
def audit_log_delete(sender, instance, **kwargs):
    from ..middleware import is_audit_logging_suppressed

    if is_audit_logging_suppressed():
        return

    monitored_models = ["Report", "Teacher", "School", "Department", "Ticket"]
    if sender.__name__ not in monitored_models:
        return

    from ..middleware import get_current_request
    request = get_current_request()
    if not request or not request.user.is_authenticated:
        return

    school = getattr(instance, "school", None)
    
    AuditLog.objects.create(
        school=school,
        teacher=request.user,
        action=AuditLog.Action.DELETE,
        model_name=sender.__name__,
        object_id=instance.pk if hasattr(instance, "pk") else None,
        object_repr=str(instance)[:255],
        ip_address=request.META.get("REMOTE_ADDR"),
        user_agent=request.META.get("HTTP_USER_AGENT", "")[:500]
    )

