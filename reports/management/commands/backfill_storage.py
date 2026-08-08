"""تهيئة/مصالحة تتبّع التخزين التزايدي للبيانات الحالية.

يحسب حجم ملفات كل سجل ويخزّنه في ``storage_bytes``، ثم يضبط إجمالي كل مدرسة
في ``School.storage_used_bytes``. يُشغّل مرة واحدة بعد ترحيل الحقول، أو عند
الاشتباه بانحراف القيم.

    python manage.py backfill_storage
"""
from __future__ import annotations

from django.core.management.base import BaseCommand

from reports.models import (
    AchievementEvidenceImage,
    AchievementEvidenceReport,
    Document,
    LeadershipEvidenceImage,
    Notification,
    Report,
    School,
    SchoolYearArchive,
    TeacherAchievementFile,
    Ticket,
    TicketImage,
)
from reports.services_archive import _file_size, recompute_school_storage


# يجب أن تطابق هذه القائمة ما يسجّله ``storage_tracking.connect_all()``؛ نموذجٌ
# ناقص هنا يُصفَّر حجمه عند كل مصالحة رغم أنه يشغل مساحة فعلية.
_MODELS = [
    (Report, ["image1", "image2", "image3", "image4"]),
    (TeacherAchievementFile, ["pdf_file"]),
    (AchievementEvidenceImage, ["image"]),
    (AchievementEvidenceReport, ["archived_image1", "archived_image2", "archived_image3", "archived_image4"]),
    (LeadershipEvidenceImage, ["image"]),
    (Document, ["file"]),
    (Ticket, ["attachment"]),
    (TicketImage, ["image"]),
    (Notification, ["attachment"]),
    (SchoolYearArchive, ["archive_file"]),
]


class Command(BaseCommand):
    help = "إعادة حساب أحجام الملفات لكل سجل وإجمالي تخزين كل مدرسة."

    def handle(self, *args, **options):
        # 1) per-record sizes
        for model, fields in _MODELS:
            updated = 0
            for obj in model.objects.all().iterator(chunk_size=200):
                total = sum(_file_size(getattr(obj, f, None)) for f in fields)
                if int(getattr(obj, "storage_bytes", 0) or 0) != total:
                    model.objects.filter(pk=obj.pk).update(storage_bytes=total)
                    updated += 1
            self.stdout.write(f"{model.__name__}: حُدّث {updated} سجل.")

        # 2) per-school totals (مسح دقيق)
        n = 0
        for school in School.objects.all().iterator(chunk_size=100):
            recompute_school_storage(school)
            n += 1
        self.stdout.write(self.style.SUCCESS(f"تمت مصالحة تخزين {n} مدرسة."))
