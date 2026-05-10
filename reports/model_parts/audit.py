from __future__ import annotations

from .base import *
from .schools import School, Teacher

class AuditLog(models.Model):
    class Action(models.TextChoices):
        CREATE = "create", "إنشاء"
        UPDATE = "update", "تعديل"
        DELETE = "delete", "حذف"
        LOGIN = "login", "تسجيل دخول"
        LOGOUT = "logout", "تسجيل خروج"

    school = models.ForeignKey(
        School,
        on_delete=models.CASCADE,
        related_name="audit_logs",
        verbose_name="المدرسة",
        null=True,
        blank=True
    )
    teacher = models.ForeignKey(
        "Teacher",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="audit_logs",
        verbose_name="المستخدم"
    )
    action = models.CharField("العملية", max_length=20, choices=Action.choices)
    model_name = models.CharField("اسم النموذج", max_length=100, blank=True)
    object_id = models.PositiveIntegerField("معرف السجل", null=True, blank=True)
    object_repr = models.CharField("وصف السجل", max_length=255, blank=True)
    changes = models.JSONField("التغييرات", null=True, blank=True)
    ip_address = models.GenericIPAddressField("عنوان IP", null=True, blank=True)
    user_agent = models.TextField("متصفح المستخدم", blank=True)
    timestamp = models.DateTimeField("الوقت", auto_now_add=True)

    class Meta:
        ordering = ("-timestamp",)
        verbose_name = "سجل عمليات"
        verbose_name_plural = "سجلات العمليات"
        indexes = [
            models.Index(fields=["school", "timestamp"]),
            models.Index(fields=["teacher", "timestamp"]),
        ]

    def __str__(self):
        return f"{self.teacher} - {self.get_action_display()} - {self.model_name} ({self.timestamp})"


# =========================
# Audit Log Signals
