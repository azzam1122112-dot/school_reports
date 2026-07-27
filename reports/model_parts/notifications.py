from __future__ import annotations

from .base import *
from .schools import School, Teacher
from .tickets import Ticket

class Notification(models.Model):
    title = models.CharField(max_length=120, blank=True, default="")
    message = models.TextField()
    is_important = models.BooleanField(default=False)
    expires_at = models.DateTimeField(null=True, blank=True)

    attachment = models.FileField(
        "مرفق (اختياري)",
        upload_to=_notification_attachment_upload_to,
        null=True,
        blank=True,
        validators=[validate_circular_attachment_file],
        help_text="يسمح بـ PDF/صور. حد أقصى 5MB.",
    )
    storage_bytes = models.PositiveBigIntegerField(
        "حجم المرفق",
        default=0,
        editable=False,
    )

    # =========================
    # التواقيع (للتعاميم الإلزامية)
    # =========================
    requires_signature = models.BooleanField(
        "يتطلب توقيع؟",
        default=False,
        help_text="عند التفعيل يصبح الإشعار تعميمًا ويتطلب إقرار + إدخال الجوال للتوقيع.",
    )
    is_broadcast = models.BooleanField(
        "للجميع (بث عام)؟",
        default=False,
        help_text="عند التفعيل يُعرض الإشعار/التعميم لجميع مستلميه دون تخصيص.",
    )
    signature_deadline_at = models.DateTimeField(
        "آخر موعد للتوقيع",
        null=True,
        blank=True,
        help_text="اختياري: يظهر للمعلمين في صفحة التوقيع ويستخدم للتقارير.",
    )
    signature_ack_text = models.TextField(
        "نص الإقرار",
        blank=True,
        default="أقرّ بأنني اطلعت على هذا التعميم وفهمت ما ورد فيه وأتعهد بالالتزام به.",
    )
    created_at = models.DateTimeField(default=timezone.now)
    school = models.ForeignKey(
        School,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="notifications",
        verbose_name="المدرسة المستهدفة",
        help_text="إن تُركت فارغة يكون الإشعار عامًا أو على مستوى كل المدارس.",
    )
    created_by = models.ForeignKey(
        Teacher, null=True, blank=True, on_delete=models.SET_NULL, related_name="notifications_created"
    )

    class Meta:
        db_table = "reports_notification"
        ordering = ("-created_at", "-id")

    # Backwards-compatible aliases for templates/legacy code.
    # Some templates reference n.body / n.content / n.text / n.details.
    # The canonical field is `message`.
    @property
    def body(self) -> str:
        return self.message

    @property
    def content(self) -> str:
        return self.message

    @property
    def text(self) -> str:
        return self.message

    @property
    def details(self) -> str:
        return self.message

    def __str__(self):
        return self.title or (self.message[:30] + ("..." if len(self.message) > 30 else ""))


class NotificationRecipient(models.Model):
    notification = models.ForeignKey(Notification, on_delete=models.CASCADE, related_name="recipients")
    teacher = models.ForeignKey(Teacher, on_delete=models.CASCADE, related_name="notifications")
    is_read = models.BooleanField(default=False)
    read_at = models.DateTimeField(null=True, blank=True)

    # توقيع التعميم (على مستوى المستلم)
    is_signed = models.BooleanField(default=False)
    signed_at = models.DateTimeField(null=True, blank=True)
    signature_attempt_count = models.PositiveSmallIntegerField(default=0)
    signature_last_attempt_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = "reports_notification_recipient"
        indexes = [
            models.Index(fields=["teacher", "is_read", "-created_at"]),
            models.Index(fields=["teacher", "is_signed", "-created_at"]),
            # ✅ فهرس لتسريع استعلامات notification__school_id عبر FK
            models.Index(fields=["notification", "teacher"]),
        ]
        unique_together = (("notification", "teacher"),)

    def __str__(self):
        return f"{self.teacher} ← {self.notification}"


# reports/models.py  (بعد class Ticket)
class TicketImage(models.Model):
    ticket = models.ForeignKey(
        Ticket,
        on_delete=models.CASCADE,
        related_name="images",
        verbose_name="التذكرة",
        db_index=True,
    )
    image = models.ImageField(
        "الصورة",
        upload_to=_ticket_image_upload_to,
        blank=False,
        null=False,
        validators=[validate_image_file],
    )
    storage_bytes = models.PositiveBigIntegerField(
        "حجم الصورة",
        default=0,
        editable=False,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["id"]
        verbose_name = "صورة تذكرة"
        verbose_name_plural = "صور التذكرة"

    def __str__(self):
        return f"TicketImage #{self.pk} for Ticket #{self.ticket_id}"


# =========================
# إدارة الاشتراكات والمالية
