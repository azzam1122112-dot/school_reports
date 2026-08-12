from __future__ import annotations

import uuid

from .base import *


class PlatformEmailConfiguration(models.Model):
    """Non-secret mailbox identity and retention settings managed by the platform owner."""

    sender_name = models.CharField("اسم المرسل", max_length=120, default="منصة توثيق")
    sender_email = models.EmailField(
        "بريد الإرسال",
        default="notifications@tawtheeq-ksa.com",
    )
    inbound_email = models.EmailField(
        "بريد الاستقبال",
        default="support@mail.tawtheeq-ksa.com",
    )
    reply_to_email = models.EmailField(
        "بريد الردود",
        default="support@mail.tawtheeq-ksa.com",
    )
    is_sending_enabled = models.BooleanField("الإرسال مفعّل", default=False)
    is_receiving_enabled = models.BooleanField("الاستقبال مفعّل", default=False)
    retention_days = models.PositiveSmallIntegerField("مدة الاحتفاظ بالأيام", default=365)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "إعداد بريد المنصة"
        verbose_name_plural = "إعدادات بريد المنصة"

    def save(self, *args, **kwargs):
        self.pk = 1
        return super().save(*args, **kwargs)

    @classmethod
    def load(cls):
        obj, _created = cls.objects.get_or_create(pk=1)
        return obj

    def __str__(self) -> str:
        return f"{self.sender_name} <{self.sender_email}>"


class PlatformEmail(models.Model):
    class Direction(models.TextChoices):
        INBOUND = "inbound", "وارد"
        OUTBOUND = "outbound", "مرسل"

    class Status(models.TextChoices):
        RECEIVED = "received", "مستلمة"
        QUEUED = "queued", "في الانتظار"
        SENT = "sent", "مرسلة"
        DELIVERED = "delivered", "تم التسليم"
        DELAYED = "delayed", "متأخرة"
        BOUNCED = "bounced", "مرتدة"
        COMPLAINED = "complained", "شكوى بريدية"
        FAILED = "failed", "فشل الإرسال"
        SUPPRESSED = "suppressed", "موقوفة"

    direction = models.CharField(max_length=12, choices=Direction.choices, db_index=True)
    status = models.CharField(max_length=16, choices=Status.choices, db_index=True)
    provider_id = models.CharField(max_length=120, blank=True, null=True, unique=True)
    message_id = models.CharField(max_length=255, blank=True, default="", db_index=True)
    thread_key = models.UUIDField(default=uuid.uuid4, db_index=True)
    parent = models.ForeignKey(
        "self",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="replies",
    )
    from_email = models.EmailField()
    from_name = models.CharField(max_length=160, blank=True, default="")
    to_emails = models.JSONField(default=list)
    cc_emails = models.JSONField(default=list, blank=True)
    bcc_emails = models.JSONField(default=list, blank=True)
    reply_to_emails = models.JSONField(default=list, blank=True)
    subject = models.CharField(max_length=500, blank=True, default="(بدون موضوع)")
    text_body = models.TextField(blank=True, default="")
    html_body = models.TextField(blank=True, default="")
    snippet = models.CharField(max_length=320, blank=True, default="")
    raw_headers = models.JSONField(default=dict, blank=True)
    provider_payload = models.JSONField(default=dict, blank=True)
    failure_reason = models.TextField(blank=True, default="")
    is_read = models.BooleanField(default=False, db_index=True)
    is_starred = models.BooleanField(default=False, db_index=True)
    is_archived = models.BooleanField(default=False, db_index=True)
    opened_count = models.PositiveIntegerField(default=0)
    clicked_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="platform_emails_created",
    )
    received_at = models.DateTimeField(null=True, blank=True)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    opened_at = models.DateTimeField(null=True, blank=True)
    clicked_at = models.DateTimeField(null=True, blank=True)
    last_event_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("-last_event_at", "-created_at", "-id")
        verbose_name = "رسالة بريد المنصة"
        verbose_name_plural = "بريد المنصة"
        indexes = [
            models.Index(fields=("direction", "is_archived", "-created_at"), name="rpt_mail_folder_idx"),
            models.Index(fields=("status", "-created_at"), name="rpt_mail_status_idx"),
        ]

    @property
    def display_sender(self) -> str:
        return self.from_name or self.from_email

    @property
    def primary_recipient(self) -> str:
        return (self.to_emails or ["—"])[0]

    @property
    def activity_at(self):
        return self.received_at or self.sent_at or self.last_event_at or self.created_at

    def __str__(self) -> str:
        return f"{self.get_direction_display()}: {self.subject}"


class PlatformEmailAttachment(models.Model):
    email = models.ForeignKey(
        PlatformEmail,
        on_delete=models.CASCADE,
        related_name="attachments",
    )
    provider_id = models.CharField(max_length=120, blank=True, default="")
    filename = models.CharField(max_length=255)
    content_type = models.CharField(max_length=160, blank=True, default="")
    content_disposition = models.CharField(max_length=40, blank=True, default="")
    content_id = models.CharField(max_length=255, blank=True, default="")
    size = models.PositiveBigIntegerField(default=0)

    class Meta:
        ordering = ("id",)
        constraints = [
            models.UniqueConstraint(
                fields=("email", "provider_id"),
                condition=~models.Q(provider_id=""),
                name="rpt_mail_attachment_provider_uq",
            )
        ]

    def __str__(self) -> str:
        return self.filename


class PlatformEmailEvent(models.Model):
    provider_event_id = models.CharField(max_length=160, unique=True)
    email = models.ForeignKey(
        PlatformEmail,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="events",
    )
    event_type = models.CharField(max_length=80, db_index=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    received_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-occurred_at", "-received_at")
        verbose_name = "حدث بريد"
        verbose_name_plural = "أحداث البريد"

    def __str__(self) -> str:
        return f"{self.event_type} ({self.provider_event_id})"
