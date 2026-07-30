from django.db import migrations, models
from django.db.models import F


def _safe_size(field_file):
    try:
        if not field_file or not field_file.name:
            return 0
        return int(field_file.size or 0)
    except Exception:
        return 0


def backfill_administrative_storage(apps, schema_editor):
    School = apps.get_model("reports", "School")
    Ticket = apps.get_model("reports", "Ticket")
    TicketImage = apps.get_model("reports", "TicketImage")
    Notification = apps.get_model("reports", "Notification")
    school_deltas = {}

    for ticket in Ticket.objects.all().iterator(chunk_size=200):
        size = _safe_size(ticket.attachment)
        if size:
            Ticket.objects.filter(pk=ticket.pk).update(storage_bytes=size)
            if ticket.school_id:
                school_deltas[ticket.school_id] = school_deltas.get(ticket.school_id, 0) + size

    for image in TicketImage.objects.select_related("ticket").all().iterator(chunk_size=200):
        size = _safe_size(image.image)
        if size:
            TicketImage.objects.filter(pk=image.pk).update(storage_bytes=size)
            school_id = getattr(image.ticket, "school_id", None)
            if school_id:
                school_deltas[school_id] = school_deltas.get(school_id, 0) + size

    for notification in Notification.objects.all().iterator(chunk_size=200):
        size = _safe_size(notification.attachment)
        if size:
            Notification.objects.filter(pk=notification.pk).update(storage_bytes=size)
            if notification.school_id:
                school_deltas[notification.school_id] = (
                    school_deltas.get(notification.school_id, 0) + size
                )

    for school_id, delta in school_deltas.items():
        School.objects.filter(pk=school_id).update(
            storage_used_bytes=F("storage_used_bytes") + delta
        )


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0066_expand_legacy_teacher_phone_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="notification",
            name="storage_bytes",
            field=models.PositiveBigIntegerField(
                default=0,
                editable=False,
                verbose_name="حجم المرفق",
            ),
        ),
        migrations.AddField(
            model_name="ticket",
            name="storage_bytes",
            field=models.PositiveBigIntegerField(
                default=0,
                editable=False,
                verbose_name="حجم المرفق",
            ),
        ),
        migrations.AddField(
            model_name="ticketimage",
            name="storage_bytes",
            field=models.PositiveBigIntegerField(
                default=0,
                editable=False,
                verbose_name="حجم الصورة",
            ),
        ),
        migrations.AddField(
            model_name="schoolyeararchive",
            name="circular_count",
            field=models.PositiveIntegerField(default=0, verbose_name="عدد التعاميم"),
        ),
        migrations.AddField(
            model_name="schoolyeararchive",
            name="notification_count",
            field=models.PositiveIntegerField(default=0, verbose_name="عدد الإشعارات"),
        ),
        migrations.AddField(
            model_name="schoolyeararchive",
            name="ticket_count",
            field=models.PositiveIntegerField(default=0, verbose_name="عدد التذاكر"),
        ),
        migrations.RunPython(
            backfill_administrative_storage,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
