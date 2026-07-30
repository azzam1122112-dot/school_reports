"""تتبّع تخزين كل مدرسة بشكل تزايدي (incremental).

الفكرة:
- لكل سجل يحوي ملفات نخزّن مجموع أحجام ملفاته في حقل ``storage_bytes``.
- نحتفظ بإجمالي تزايدي على مستوى المدرسة في ``School.storage_used_bytes``.
- عند الفحص (مسار ساخن ومتكرر) نقرأ ``School.storage_used_bytes`` فقط — **بلا أي
  طلبات شبكية (HEAD) إلى التخزين**.

التحديث يتم عبر إشارات (signals):
- ``pre_save``  : نلتقط الحجم القديم، ونعيد حساب حجم السجل فقط إن تغيّر أحد ملفاته
                  (مقارنة بالاسم بلا قراءة حجم)، فالملفات الجديدة حجمها متاح في الذاكرة.
- ``post_save`` : نطبّق الفرق على إجمالي المدرسة ونثبّت ``storage_bytes``.
- ``pre_delete``: نلتقط المدرسة والحجم قبل الحذف (مهم للحذف المتسلسل cascade).
- ``post_delete``: نخصم الحجم من إجمالي المدرسة.
"""
from __future__ import annotations

import logging

from django.db.models import F
from django.db.models.signals import post_delete, post_save, pre_delete, pre_save
from django.dispatch import receiver

logger = logging.getLogger(__name__)


def _safe_size(fieldfile) -> int:
    """حجم ملف بأمان. للملفات المرفوعة حديثًا يكون متاحًا في الذاكرة (بلا شبكة)."""
    if not fieldfile:
        return 0
    try:
        if not getattr(fieldfile, "name", ""):
            return 0
    except Exception:
        return 0
    try:
        return int(fieldfile.size or 0)
    except Exception:
        return 0


def _name_of(fieldfile) -> str:
    try:
        return getattr(fieldfile, "name", "") or ""
    except Exception:
        return ""


def _compute_record_bytes(instance, fields) -> int:
    return sum(_safe_size(getattr(instance, f, None)) for f in fields)


def _apply_school_delta(school_id, delta: int) -> None:
    if not school_id or not delta:
        return
    try:
        from .models import School

        # تحديث ذرّي عبر F() لتفادي حالات التسابق
        School.objects.filter(pk=school_id).update(
            storage_used_bytes=F("storage_used_bytes") + delta
        )
        # حماية من القيم السالبة الناتجة عن انحراف تاريخي
        School.objects.filter(pk=school_id, storage_used_bytes__lt=0).update(
            storage_used_bytes=0
        )
    except Exception:
        logger.exception("storage_tracking: failed to apply school delta")


def register(model, *, fields, school_id_getter):
    """يربط إشارات التتبّع بنموذج يحوي ملفات."""

    @receiver(pre_save, sender=model, weak=False)
    def _pre_save(sender, instance, **kwargs):
        if kwargs.get("raw"):
            return

        old_bytes = 0
        old_school_id = None
        files_changed = instance.pk is None  # جديد = نحسب دائمًا
        if instance.pk is not None:
            try:
                old = sender.objects.filter(pk=instance.pk).first()
            except Exception:
                old = None
            if old is not None:
                old_bytes = int(getattr(old, "storage_bytes", 0) or 0)
                try:
                    old_school_id = school_id_getter(old)
                except Exception:
                    old_school_id = None
                # هل تغيّر أي ملف؟ مقارنة بالاسم فقط (بلا قراءة حجم/شبكة)
                files_changed = any(
                    _name_of(getattr(instance, f, None)) != _name_of(getattr(old, f, None))
                    for f in fields
                )
        instance._st_old_bytes = old_bytes
        instance._st_old_school_id = old_school_id
        if files_changed:
            new_bytes = _compute_record_bytes(instance, fields)
            instance.storage_bytes = new_bytes
            instance._st_delta = new_bytes - old_bytes
        else:
            # لا تغيير في الملفات → لا فرق ولا قراءة شبكية
            instance._st_delta = 0

    @receiver(post_save, sender=model, weak=False)
    def _post_save(sender, instance, **kwargs):
        if kwargs.get("raw"):
            return

        delta = int(getattr(instance, "_st_delta", 0) or 0)
        try:
            new_school_id = school_id_getter(instance)
        except Exception:
            new_school_id = None
        old_school_id = getattr(instance, "_st_old_school_id", None)
        if old_school_id != new_school_id:
            old_bytes = int(getattr(instance, "_st_old_bytes", 0) or 0)
            new_bytes = int(getattr(instance, "storage_bytes", 0) or 0)
            _apply_school_delta(old_school_id, -old_bytes)
            _apply_school_delta(new_school_id, new_bytes)
            return
        _apply_school_delta(new_school_id, delta)

    @receiver(pre_delete, sender=model, weak=False)
    def _pre_delete(sender, instance, **kwargs):
        # نلتقط المدرسة الآن لأن العلاقات قد تُحذف لاحقًا في الحذف المتسلسل
        try:
            instance._st_del_school_id = school_id_getter(instance)
        except Exception:
            instance._st_del_school_id = None
        instance._st_del_bytes = int(getattr(instance, "storage_bytes", 0) or 0)

    @receiver(post_delete, sender=model, weak=False)
    def _post_delete(sender, instance, **kwargs):
        sid = getattr(instance, "_st_del_school_id", None)
        bytes_ = int(getattr(instance, "_st_del_bytes", 0) or 0)
        if sid and bytes_:
            _apply_school_delta(sid, -bytes_)


def _evidence_section_school_id(instance):
    """يحلّ معرّف المدرسة لشواهد الإنجاز عبر القسم → الملف → المدرسة."""
    sid = getattr(instance, "section_id", None)
    if not sid:
        return None
    try:
        from .models import AchievementSection

        return (
            AchievementSection.objects.filter(pk=sid)
            .values_list("file__school_id", flat=True)
            .first()
        )
    except Exception:
        return None


def connect_all():
    """يُستدعى من apps.ready() لربط جميع النماذج ذات الملفات."""
    from .models import (
        AchievementEvidenceImage,
        AchievementEvidenceReport,
        Notification,
        Report,
        SchoolYearArchive,
        TeacherAchievementFile,
        Ticket,
        TicketImage,
    )

    register(Report, fields=["image1", "image2", "image3", "image4"],
             school_id_getter=lambda i: getattr(i, "school_id", None))
    register(TeacherAchievementFile, fields=["pdf_file"],
             school_id_getter=lambda i: getattr(i, "school_id", None))
    register(AchievementEvidenceImage, fields=["image"],
             school_id_getter=_evidence_section_school_id)
    register(AchievementEvidenceReport,
             fields=["archived_image1", "archived_image2", "archived_image3", "archived_image4"],
             school_id_getter=_evidence_section_school_id)
    register(SchoolYearArchive, fields=["archive_file"],
             school_id_getter=lambda i: getattr(i, "school_id", None))
    register(Ticket, fields=["attachment"],
             school_id_getter=lambda i: getattr(i, "school_id", None))
    register(TicketImage, fields=["image"],
             school_id_getter=lambda i: getattr(getattr(i, "ticket", None), "school_id", None))
    register(Notification, fields=["attachment"],
             school_id_getter=lambda i: getattr(i, "school_id", None))
