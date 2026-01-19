from __future__ import annotations

import gzip
import json
from datetime import timedelta
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.utils import timezone


class Command(BaseCommand):
    help = "Delete (and optionally archive) AuditLog rows older than N days."

    def add_arguments(self, parser):
        parser.add_argument(
            "--days",
            type=int,
            default=int(getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 30)),
            help="Retention window in days (default: settings.AUDIT_LOG_RETENTION_DAYS or 30)",
        )
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Only show how many rows would be affected.",
        )
        parser.add_argument(
            "--archive",
            action="store_true",
            help="Archive rows to a .jsonl.gz file before deleting.",
        )
        parser.add_argument(
            "--archive-dir",
            type=str,
            default="",
            help="Directory to write archives into (default: MEDIA_ROOT/audit_archives).",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=2000,
            help="How many rows to process per batch.",
        )

    def handle(self, *args, **options):
        from reports.models import AuditLog

        days: int = max(int(options["days"]), 0)
        dry_run: bool = bool(options["dry_run"])
        archive: bool = bool(options["archive"])
        chunk_size: int = max(int(options["chunk_size"]), 100)

        cutoff = timezone.now() - timedelta(days=days)

        qs = AuditLog.objects.filter(timestamp__lt=cutoff).order_by("pk")

        # Count can be expensive, but it's useful feedback.
        total = qs.count()
        self.stdout.write(
            self.style.WARNING(
                f"AuditLog cleanup: {total} rows older than {days} days (cutoff: {cutoff:%Y-%m-%d %H:%M})."
            )
        )

        if dry_run:
            self.stdout.write(self.style.SUCCESS("Dry-run complete; no changes made."))
            return

        archive_path: Path | None = None
        gzip_fp = None
        if archive and total:
            if options.get("archive_dir"):
                archive_dir = Path(str(options["archive_dir"]))
            else:
                archive_dir = Path(getattr(settings, "MEDIA_ROOT", ".")) / "audit_archives"

            archive_dir.mkdir(parents=True, exist_ok=True)
            ts = timezone.now().strftime("%Y%m%d_%H%M%S")
            archive_path = archive_dir / f"auditlog_archive_before_{ts}.jsonl.gz"
            gzip_fp = gzip.open(archive_path, mode="wt", encoding="utf-8")

            meta = {
                "_meta": {
                    "model": "reports.AuditLog",
                    "cutoff": cutoff.isoformat(),
                    "days": days,
                    "generated_at": timezone.now().isoformat(),
                }
            }
            gzip_fp.write(json.dumps(meta, ensure_ascii=False) + "\n")

        deleted_total = 0
        archived_total = 0

        try:
            while True:
                batch_pks = list(qs.values_list("pk", flat=True)[:chunk_size])
                if not batch_pks:
                    break

                batch_qs = AuditLog.objects.filter(pk__in=batch_pks)

                if gzip_fp is not None:
                    for row in batch_qs.values(
                        "id",
                        "school_id",
                        "teacher_id",
                        "action",
                        "model_name",
                        "object_id",
                        "object_repr",
                        "changes",
                        "ip_address",
                        "user_agent",
                        "timestamp",
                    ).iterator(chunk_size=chunk_size):
                        # Ensure JSON-serializable timestamp
                        ts = row.get("timestamp")
                        if ts is not None:
                            row["timestamp"] = ts.isoformat()
                        gzip_fp.write(json.dumps(row, ensure_ascii=False) + "\n")
                        archived_total += 1

                # AuditLog has no expected dependent rows; delete() returns (count, details)
                deleted, _ = batch_qs.delete()
                deleted_total += int(deleted)

        finally:
            if gzip_fp is not None:
                gzip_fp.close()

        if archive_path is not None and archived_total:
            self.stdout.write(self.style.SUCCESS(f"Archived {archived_total} rows to: {archive_path}"))

        self.stdout.write(self.style.SUCCESS(f"Deleted {deleted_total} rows."))
