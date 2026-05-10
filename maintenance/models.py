from __future__ import annotations

from django.conf import settings
from django.db import models
from django.utils import timezone


class SchoolYearResetJob(models.Model):
    class Status(models.TextChoices):
        DRAFT = "draft", "مسودة"
        PREVIEWED = "previewed", "تمت المعاينة"
        RUNNING = "running", "قيد التنفيذ"
        COMPLETED = "completed", "مكتملة"
        PARTIAL = "partial", "مكتملة جزئياً"
        FAILED = "failed", "فشلت"

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="school_year_reset_jobs",
        verbose_name="أنشئت بواسطة",
    )
    schools = models.ManyToManyField(
        "reports.School",
        blank=True,
        related_name="year_reset_jobs",
        verbose_name="المدارس المستهدفة",
    )
    status = models.CharField(
        "الحالة",
        max_length=20,
        choices=Status.choices,
        default=Status.DRAFT,
        db_index=True,
    )
    include_reports = models.BooleanField("حذف التقارير", default=True)
    include_tickets = models.BooleanField("حذف الطلبات/التذاكر", default=True)
    include_achievements = models.BooleanField("حذف ملفات الإنجاز", default=True)
    include_notifications = models.BooleanField("حذف التعاميم والإشعارات", default=True)
    include_share_links = models.BooleanField("حذف روابط المشاركة", default=True)
    delete_files = models.BooleanField("حذف المرفقات من التخزين", default=False)

    dry_run_summary = models.JSONField("ملخص المعاينة", default=dict, blank=True)
    execution_summary = models.JSONField("ملخص التنفيذ", default=dict, blank=True)
    file_manifest = models.JSONField("Manifest الملفات", default=dict, blank=True)

    started_at = models.DateTimeField("بدأت في", null=True, blank=True)
    finished_at = models.DateTimeField("انتهت في", null=True, blank=True)
    error_message = models.TextField("رسالة الخطأ", blank=True, default="")
    created_at = models.DateTimeField("أنشئت في", auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "عملية تهيئة عام دراسي"
        verbose_name_plural = "عمليات تهيئة العام الدراسي"

    def mark_running(self) -> None:
        self.status = self.Status.RUNNING
        self.started_at = timezone.now()
        self.error_message = ""
        self.save(update_fields=["status", "started_at", "error_message"])

    def __str__(self) -> str:
        return f"SchoolYearResetJob #{self.pk} - {self.get_status_display()}"
