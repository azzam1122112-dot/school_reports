"""Find and optionally delete legacy unreferenced media objects.

Dry-run is the default. Actual deletion requires ``--delete``.
"""
from __future__ import annotations

from collections import defaultdict

from django.core.management.base import BaseCommand, CommandError

from reports.file_cleanup import (
    _all_file_fields,
    _storage_signature,
)


SAFE_PREFIXES = (
    "achievements",
    "notifications",
    "payments/receipts",
    "reports",
    "school-archives",
    "tickets",
)


def _walk(storage, prefix):
    try:
        directories, files = storage.listdir(prefix)
    except Exception:
        return
    for filename in files:
        yield f"{prefix.rstrip('/')}/{filename}"
    for directory in directories:
        child = f"{prefix.rstrip('/')}/{directory}"
        yield from _walk(storage, child)


class Command(BaseCommand):
    help = (
        "معاينة الملفات اليتيمة في التخزين أو حذفها. "
        "الوضع الافتراضي آمن ولا يحذف؛ استخدم --delete للحذف الفعلي."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--delete",
            action="store_true",
            help="حذف الملفات اليتيمة فعليًا. بدون هذا الخيار تُعرض معاينة فقط.",
        )
        parser.add_argument(
            "--prefix",
            action="append",
            choices=SAFE_PREFIXES,
            help="قصر الفحص على مسار آمن محدد. يمكن تكراره.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=10000,
            help="أقصى عدد كائنات تُفحص في التشغيل الواحد (الافتراضي 10000).",
        )

    def handle(self, *args, **options):
        prefixes = tuple(options.get("prefix") or SAFE_PREFIXES)
        should_delete = bool(options.get("delete"))
        limit = int(options.get("limit") or 0)
        if limit <= 0:
            raise CommandError("--limit يجب أن يكون أكبر من صفر.")

        storage_groups = {}
        fields_by_storage = defaultdict(list)
        for model, field in _all_file_fields():
            signature = _storage_signature(field.storage)
            storage_groups.setdefault(signature, field.storage)
            fields_by_storage[signature].append((model, field))

        scanned = 0
        orphaned = 0
        deleted = 0
        bytes_reclaimed = 0
        stop = False

        for signature, storage in storage_groups.items():
            referenced = set()
            for model, field in fields_by_storage[signature]:
                try:
                    referenced.update(
                        name
                        for name in model._default_manager.exclude(
                            **{field.name: ""}
                        )
                        .exclude(**{f"{field.name}__isnull": True})
                        .values_list(field.name, flat=True)
                        .iterator(chunk_size=1000)
                        if name
                    )
                except Exception as exc:
                    raise CommandError(
                        f"تعذر قراءة مراجع {model._meta.label}.{field.name}: {exc}"
                    ) from exc

            for prefix in prefixes:
                for name in _walk(storage, prefix):
                    scanned += 1
                    if scanned > limit:
                        stop = True
                        break
                    if name in referenced:
                        continue

                    orphaned += 1
                    try:
                        size = int(storage.size(name) or 0)
                    except Exception:
                        size = 0
                    self.stdout.write(
                        f"{'[حذف]' if should_delete else '[معاينة]'} "
                        f"{name} ({size} بايت)"
                    )
                    if should_delete:
                        storage.delete(name)
                        deleted += 1
                        bytes_reclaimed += size
                if stop:
                    break
            if stop:
                break

        mode = "حذف فعلي" if should_delete else "معاينة فقط"
        summary = (
            f"{mode}: فُحص {min(scanned, limit)} ملف، "
            f"وُجد {orphaned} ملف يتيم، حُذف {deleted}، "
            f"تم توفير {bytes_reclaimed} بايت."
        )
        if stop:
            summary += " تم الوصول إلى حد الفحص؛ شغّل الأمر مجددًا بحد أعلى."
        if should_delete:
            self.stdout.write(self.style.SUCCESS(summary))
        else:
            self.stdout.write(self.style.WARNING(summary))
            self.stdout.write(
                "لم يُحذف أي ملف. راجع القائمة ثم استخدم --delete عند الاعتماد."
            )
