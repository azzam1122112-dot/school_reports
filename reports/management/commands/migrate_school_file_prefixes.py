"""Move active database file references into canonical school prefixes.

The command is deliberately resumable and non-destructive: it copies first,
verifies the destination size, and only then updates the database reference.
The legacy source is retained for the existing orphan-cleanup workflow.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from typing import Callable

from django.core.exceptions import ObjectDoesNotExist
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils.text import get_valid_filename

from reports.models import (
    AchievementEvidenceImage,
    AchievementEvidenceReport,
    AssignmentEvidence,
    Document,
    GeneratedExportJob,
    LeadershipEvidenceImage,
    Notification,
    Payment,
    Report,
    ReportEvidence,
    SchoolYearArchive,
    TeacherAchievementFile,
    Ticket,
    TicketImage,
)
from reports.school_storage import is_in_school_prefix, safe_segment, school_storage_key


@dataclass(frozen=True)
class FileSpec:
    model: type
    fields: tuple[str, ...]
    category: str
    select_related: tuple[str, ...]
    school_getter: Callable


SPECS = (
    FileSpec(Report, ("image1", "image2", "image3", "image4"), "reports/images", ("school",), lambda obj: obj.school),
    FileSpec(ReportEvidence, ("image",), "reports/evidence", ("report__school",), lambda obj: obj.report.school),
    FileSpec(TeacherAchievementFile, ("pdf_file",), "achievements/pdfs", ("school",), lambda obj: obj.school),
    FileSpec(AchievementEvidenceImage, ("image",), "achievements/evidence", ("section__file__school",), lambda obj: obj.section.file.school),
    FileSpec(AchievementEvidenceReport, ("archived_image1", "archived_image2", "archived_image3", "archived_image4"), "achievements/report-evidence", ("section__file__school",), lambda obj: obj.section.file.school),
    FileSpec(LeadershipEvidenceImage, ("image",), "leadership/evidence", ("section__portfolio__school",), lambda obj: obj.section.portfolio.school),
    FileSpec(AssignmentEvidence, ("file",), "assignments/evidence", ("target__school", "target__assignment__school"), lambda obj: obj.target.school or obj.target.assignment.school),
    FileSpec(Document, ("file",), "documents", ("school",), lambda obj: obj.school),
    FileSpec(Notification, ("attachment",), "notifications/attachments", ("school",), lambda obj: obj.school),
    FileSpec(Ticket, ("attachment",), "tickets/attachments", ("school",), lambda obj: obj.school),
    FileSpec(TicketImage, ("image",), "tickets/images", ("ticket__school",), lambda obj: obj.ticket.school),
    FileSpec(Payment, ("receipt_image",), "payments/receipts", ("school",), lambda obj: obj.school),
    FileSpec(SchoolYearArchive, ("archive_file",), "archives", ("school",), lambda obj: obj.school),
    FileSpec(GeneratedExportJob, ("artifact_file",), "exports", ("school",), lambda obj: obj.school),
)


def destination_name(school, category: str, source_name: str) -> str:
    """Create the same destination on every retry for safe resumption."""
    normalized_source = str(source_name or "").lstrip("/")
    digest = hashlib.sha256(normalized_source.encode("utf-8")).hexdigest()[:16]
    basename = os.path.basename(normalized_source.replace("\\", "/")) or "file"
    basename = (get_valid_filename(basename) or "file")[-140:]
    category_parts = [safe_segment(part, fallback="files") for part in category.split("/")]
    return "/".join(
        ["schools", school_storage_key(school), *category_parts, "migrated", f"{digest}-{basename}"]
    )


class Command(BaseCommand):
    help = (
        "ينقل مراجع ملفات المدارس إلى schools/<storage_key>/ بعد النسخ والتحقق. "
        "الوضع الافتراضي معاينة فقط، واستخدم --apply للتنفيذ."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--apply",
            action="store_true",
            help="ينسخ الملفات ويتحقق منها ثم يحدّث مراجع قاعدة البيانات.",
        )
        parser.add_argument(
            "--school-id",
            action="append",
            type=int,
            dest="school_ids",
            help="يحصر التنفيذ في مدرسة؛ يمكن تكراره.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=0,
            help="الحد الأقصى لعدد مراجع الملفات التي ستُفحص (0 = الكل).",
        )

    def handle(self, *args, **options):
        apply_changes = bool(options["apply"])
        school_ids_option = options.get("school_ids") or []
        if isinstance(school_ids_option, int):
            school_ids_option = [school_ids_option]
        school_ids = set(school_ids_option)
        limit = max(0, int(options.get("limit") or 0))
        counters = {"checked": 0, "planned": 0, "migrated": 0, "skipped": 0, "errors": 0}

        self.stdout.write(
            self.style.WARNING("وضع التنفيذ الفعلي" if apply_changes else "وضع المعاينة — لن تُكتب أي تغييرات")
        )

        stop = False
        for spec in SPECS:
            # _base_manager includes archived/soft-deleted rows whose files still
            # consume storage (Report.objects intentionally hides trashed rows).
            queryset = spec.model._base_manager.all().select_related(*spec.select_related).order_by("pk")
            for obj in queryset.iterator(chunk_size=100):
                try:
                    school = spec.school_getter(obj)
                except (AttributeError, ObjectDoesNotExist):
                    school = None
                if school is None or (school_ids and school.pk not in school_ids):
                    continue

                for field_name in spec.fields:
                    field_file = getattr(obj, field_name, None)
                    source_name = str(getattr(field_file, "name", "") or "").lstrip("/")
                    if not source_name:
                        continue
                    if limit and counters["checked"] >= limit:
                        stop = True
                        break

                    counters["checked"] += 1
                    if is_in_school_prefix(source_name, school):
                        counters["skipped"] += 1
                        continue

                    target_name = destination_name(school, spec.category, source_name)
                    counters["planned"] += 1
                    label = f"{spec.model.__name__}#{obj.pk}.{field_name}"
                    self.stdout.write(f"{label}: {source_name} -> {target_name}")
                    if not apply_changes:
                        continue

                    try:
                        storage = field_file.storage
                        if not storage.exists(source_name):
                            raise FileNotFoundError(f"المصدر غير موجود: {source_name}")
                        source_size = int(storage.size(source_name))

                        if storage.exists(target_name):
                            saved_name = target_name
                        else:
                            with storage.open(source_name, "rb") as source:
                                saved_name = storage.save(target_name, source)

                        if not storage.exists(saved_name):
                            raise OSError(f"لم يظهر الملف بعد النسخ: {saved_name}")
                        target_size = int(storage.size(saved_name))
                        if source_size != target_size:
                            raise OSError(
                                f"فشل تحقق الحجم للمصدر ({source_size}) والوجهة ({target_size})"
                            )

                        with transaction.atomic():
                            updated = spec.model._base_manager.filter(
                                pk=obj.pk,
                                **{field_name: source_name},
                            ).update(
                                **{field_name: saved_name}
                            )
                            if updated != 1:
                                raise RuntimeError("تغيّر مرجع الملف بالتزامن؛ لم تُحدّث قاعدة البيانات")
                        setattr(obj, field_name, saved_name)
                        counters["migrated"] += 1
                    except Exception as exc:
                        counters["errors"] += 1
                        self.stderr.write(self.style.ERROR(f"{label}: {exc}"))

                if stop:
                    break
            if stop:
                break

        summary = (
            f"فُحص {counters['checked']}، يحتاج نقل {counters['planned']}، "
            f"نُقل {counters['migrated']}، داخل المسار مسبقًا {counters['skipped']}، "
            f"أخطاء {counters['errors']}."
        )
        if counters["errors"]:
            raise CommandError(summary)
        self.stdout.write(self.style.SUCCESS(summary))
        if apply_changes and counters["migrated"]:
            self.stdout.write(
                "احتُفظ بالملفات المصدر القديمة عمدًا. بعد مدة الأمان استخدم فحص الملفات اليتيمة لتنظيفها."
            )
