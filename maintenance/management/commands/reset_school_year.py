from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from maintenance.models import SchoolYearResetJob
from maintenance.services import (
    CONFIRM_PHRASE,
    INCLUDE_KEYS,
    build_file_manifest,
    collect_file_keys,
    collect_reset_summary,
    execute_school_year_reset,
    normalize_include_options,
    resolve_target_schools,
)


class Command(BaseCommand):
    help = "تهيئة العام الدراسي بحذف البيانات التشغيلية للمدارس المحددة فقط."

    def add_arguments(self, parser):
        mode = parser.add_mutually_exclusive_group(required=True)
        mode.add_argument("--dry-run", action="store_true", help="معاينة فقط بدون حذف.")
        mode.add_argument("--execute", action="store_true", help="تنفيذ الحذف فعلياً.")

        parser.add_argument("--all-schools", action="store_true", help="استهداف جميع المدارس.")
        parser.add_argument("--school-id", action="append", default=[], help="ID مدرسة، يمكن تكراره.")
        parser.add_argument("--school-code", action="append", default=[], help="كود مدرسة، يمكن تكراره.")
        parser.add_argument(
            "--include",
            default="reports,tickets,achievements,notifications,share_links",
            help="قائمة مفصولة بفواصل: reports,tickets,achievements,notifications,share_links",
        )
        files = parser.add_mutually_exclusive_group()
        files.add_argument("--delete-files", action="store_true", help="حذف المرفقات من التخزين.")
        files.add_argument("--skip-files", action="store_true", help="عدم حذف المرفقات من التخزين.")
        parser.add_argument("--batch-size", type=int, default=500)
        parser.add_argument("--confirm", default="", help=f"يجب أن تساوي {CONFIRM_PHRASE} عند التنفيذ.")

    def _include_options(self, raw: str) -> dict[str, bool]:
        requested = {part.strip() for part in (raw or "").split(",") if part.strip()}
        unknown = requested - set(INCLUDE_KEYS)
        if unknown:
            raise CommandError(f"خيارات include غير معروفة: {', '.join(sorted(unknown))}")
        return normalize_include_options({key: key in requested for key in INCLUDE_KEYS})

    def _print_summary(self, summary: dict) -> None:
        self.stdout.write(self.style.WARNING("ملخص تهيئة العام الدراسي:"))
        rows = [
            ("عدد المدارس", summary.get("schools_count", 0)),
            ("التقارير", summary.get("reports_count", 0)),
            ("الطلبات/التذاكر", summary.get("tickets_count", 0)),
            ("صور التذاكر", summary.get("ticket_images_count", 0)),
            ("ملفات الإنجاز", summary.get("achievements_count", 0)),
            ("صور شواهد الإنجاز", summary.get("achievement_evidence_images_count", 0)),
            ("تقارير الإنجاز المؤرشفة", summary.get("achievement_evidence_reports_count", 0)),
            ("التعاميم/الإشعارات", summary.get("notifications_count", 0)),
            ("مستلمي الإشعارات", summary.get("notification_recipients_count", 0)),
            ("روابط المشاركة", summary.get("share_links_count", 0)),
            ("الملفات المرشحة للحذف", summary.get("file_keys_count", 0)),
        ]
        for label, value in rows:
            self.stdout.write(f"- {label}: {value}")

        samples = summary.get("file_key_samples") or []
        if samples:
            self.stdout.write("أمثلة ملفات:")
            for key in samples[:20]:
                self.stdout.write(f"  - {key}")
        self.stdout.write(self.style.WARNING(summary.get("protected_data_note", "")))

    def handle(self, *args, **options):
        if options["execute"] and options.get("confirm") != CONFIRM_PHRASE:
            raise CommandError(f"للتنفيذ الفعلي يجب تمرير --confirm {CONFIRM_PHRASE}")

        if not options["all_schools"] and not options["school_id"] and not options["school_code"]:
            raise CommandError("حدد --all-schools أو --school-id أو --school-code.")

        include_options = self._include_options(options["include"])
        schools_qs = resolve_target_schools(
            all_schools=options["all_schools"],
            school_ids=options["school_id"],
            school_codes=options["school_code"],
        )
        schools = list(schools_qs)
        if not schools:
            raise CommandError("لم يتم العثور على مدارس مطابقة.")

        delete_files = bool(options["delete_files"] and not options["skip_files"])
        summary = collect_reset_summary(schools, include_options)
        file_keys = collect_file_keys(schools, include_options)
        manifest = build_file_manifest(file_keys)

        with transaction.atomic():
            job = SchoolYearResetJob.objects.create(
                status=SchoolYearResetJob.Status.DRAFT,
                include_reports=include_options["reports"],
                include_tickets=include_options["tickets"],
                include_achievements=include_options["achievements"],
                include_notifications=include_options["notifications"],
                include_share_links=include_options["share_links"],
                delete_files=delete_files,
                dry_run_summary=summary,
                file_manifest=manifest,
            )
            job.schools.set(schools)

        self._print_summary(summary)
        self.stdout.write(f"Job ID: {job.id}")

        if options["dry_run"]:
            job.status = SchoolYearResetJob.Status.PREVIEWED
            job.save(update_fields=["status"])
            self.stdout.write(self.style.SUCCESS("Dry run فقط: لم يتم حذف أي بيانات."))
            return

        execution = execute_school_year_reset(job, batch_size=options["batch_size"])
        self.stdout.write(self.style.SUCCESS(f"تم التنفيذ. الحالة: {job.get_status_display()}"))
        self.stdout.write(f"ملفات التخزين: {execution.get('files', {})}")
