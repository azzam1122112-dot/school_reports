from __future__ import annotations

from .base import *


class CustomerComplaint(models.Model):
    """Public customer complaint retained for follow-up and audit."""

    class Status(models.TextChoices):
        NEW = "new", "جديدة"
        IN_PROGRESS = "in_progress", "قيد المعالجة"
        RESOLVED = "resolved", "تمت المعالجة"
        CLOSED = "closed", "مغلقة"

    name = models.CharField("الاسم", max_length=150)
    email = models.EmailField("البريد الإلكتروني")
    phone = models.CharField("رقم الجوال", max_length=30, blank=True, default="")
    order_reference = models.CharField(
        "مرجع الطلب أو الاشتراك",
        max_length=100,
        blank=True,
        default="",
    )
    subject = models.CharField("موضوع الشكوى", max_length=180)
    message = models.TextField("تفاصيل الشكوى", max_length=5000)
    status = models.CharField(
        "الحالة",
        max_length=20,
        choices=Status.choices,
        default=Status.NEW,
        db_index=True,
    )
    internal_notes = models.TextField("ملاحظات المعالجة", blank=True, default="")
    created_at = models.DateTimeField("تاريخ الاستلام", auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField("آخر تحديث", auto_now=True)
    resolved_at = models.DateTimeField("تاريخ المعالجة", null=True, blank=True)

    class Meta:
        ordering = ("-created_at", "-id")
        verbose_name = "شكوى عميل"
        verbose_name_plural = "شكاوى العملاء"
        indexes = [
            models.Index(fields=("status", "-created_at"), name="reports_cc_status_date_idx"),
        ]

    @property
    def reference(self) -> str:
        created = self.created_at or timezone.now()
        return f"CMP-{created:%Y%m%d}-{self.pk:06d}" if self.pk else "CMP-PENDING"

    def __str__(self) -> str:
        return f"{self.reference} — {self.subject}"

