import datetime
import tempfile
from io import StringIO
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.base import ContentFile
from django.core.files.storage import FileSystemStorage
from django.core.management import call_command
from django.test import SimpleTestCase, TransactionTestCase

from reports.model_parts.achievements import _achievement_report_evidence_upload_to
from reports.model_parts.assignments import _assignment_evidence_upload_to
from reports.model_parts.base import (
    _achievement_evidence_upload_to,
    _achievement_pdf_upload_to,
    _leadership_evidence_upload_to,
    _notification_attachment_upload_to,
    _payment_receipt_upload_to,
    _report_evidence_upload_to,
    _report_image_upload_to,
    _ticket_attachment_upload_to,
    _ticket_image_upload_to,
)
from reports.model_parts.billing import generated_export_upload_to, school_year_archive_upload_to
from reports.model_parts.documents import _document_upload_to
from reports.models import Report, School, Teacher
from reports.school_storage import school_file_path


class SchoolUploadPathTests(SimpleTestCase):
    def setUp(self):
        self.school = SimpleNamespace(pk=7, code="changed-code", storage_key="fixed-school")

    def assert_school_path(self, path, category):
        self.assertTrue(
            path.startswith(f"schools/fixed-school/{category}/"),
            path,
        )
        self.assertNotIn("\\", path)

    def test_all_school_owned_uploads_share_one_immutable_root(self):
        report = SimpleNamespace(school=self.school, teacher_id=11)
        achievement_file = SimpleNamespace(
            school=self.school,
            teacher_id=11,
            academic_year="1447-1448",
        )
        achievement_section = SimpleNamespace(
            file=achievement_file,
            file_id=13,
            code="leadership",
        )
        portfolio = SimpleNamespace(school=self.school, academic_year="1447-1448")
        target = SimpleNamespace(
            school=self.school,
            assignment_id=17,
            assignment=SimpleNamespace(school=self.school),
        )

        cases = (
            (_report_image_upload_to(report, "photo.PNG"), "reports/images"),
            (_report_evidence_upload_to(SimpleNamespace(report=report), "proof.png"), "reports/evidence"),
            (_achievement_pdf_upload_to(achievement_file, "file.pdf"), "achievements/pdfs"),
            (_achievement_evidence_upload_to(SimpleNamespace(section=achievement_section), "proof.png"), "achievements/evidence"),
            (_achievement_report_evidence_upload_to(SimpleNamespace(section=achievement_section, pk=3), "frozen.png"), "achievements/report-evidence"),
            (_leadership_evidence_upload_to(SimpleNamespace(section=SimpleNamespace(portfolio=portfolio, code="axis")), "axis.png"), "leadership/evidence"),
            (_assignment_evidence_upload_to(SimpleNamespace(target=target), "work.pdf"), "assignments/evidence"),
            (_document_upload_to(SimpleNamespace(school=self.school, academic_year="1447-1448"), "document.pdf"), "documents"),
            (_notification_attachment_upload_to(SimpleNamespace(school=self.school), "circular.pdf"), "notifications/attachments"),
            (_ticket_attachment_upload_to(SimpleNamespace(school=self.school), "ticket.pdf"), "tickets/attachments"),
            (_ticket_image_upload_to(SimpleNamespace(ticket=SimpleNamespace(school=self.school)), "ticket.png"), "tickets/images"),
            (_payment_receipt_upload_to(SimpleNamespace(school=self.school), "receipt.png"), "payments/receipts"),
            (school_year_archive_upload_to(SimpleNamespace(school=self.school, academic_year="1447-1448"), "archive.zip"), "archives"),
            (generated_export_upload_to(SimpleNamespace(school=self.school), "export.xlsx"), "exports"),
        )
        for path, category in cases:
            with self.subTest(category=category):
                self.assert_school_path(path, category)

    def test_platform_files_are_explicitly_separated(self):
        ticket_path = _ticket_attachment_upload_to(SimpleNamespace(school=None), "support.pdf")
        report_path = _report_image_upload_to(
            SimpleNamespace(school=None, teacher_id=1),
            "legacy.png",
        )
        self.assertTrue(ticket_path.startswith("platform/tickets/attachments/"), ticket_path)
        self.assertTrue(report_path.startswith("platform/reports/images/"), report_path)

    def test_filename_cannot_escape_school_prefix(self):
        path = school_file_path(self.school, "documents", "../../outside.PDF")
        self.assert_school_path(path, "documents")
        self.assertNotIn("..", path)


class SchoolStorageKeyTests(TransactionTestCase):
    def test_storage_key_is_initialized_once_and_survives_code_changes(self):
        school = School.objects.create(name="مدرسة", code="first-code")
        self.assertEqual(school.storage_key, "first-code")

        school.code = "renamed-code"
        school.storage_key = "attempted-change"
        school.save()
        school.refresh_from_db()

        self.assertEqual(school.code, "renamed-code")
        self.assertEqual(school.storage_key, "first-code")


class MigrateSchoolFilePrefixesCommandTests(TransactionTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.storage = FileSystemStorage(location=self.temp_dir.name)
        self.field = Report._meta.get_field("image1")
        self.original_storage = self.field.storage
        self.field.storage = self.storage
        self.addCleanup(self._restore_storage)

        self.school = School.objects.create(name="مدرسة النقل", code="migration-school")
        self.teacher = Teacher.objects.create_user(
            phone="0500007711",
            name="معلم النقل",
            password="safe-password",
        )
        with patch("reports.utils.run_task_safe"):
            self.report = Report.objects.create(
                school=self.school,
                teacher=self.teacher,
                title="تقرير قديم",
                report_date=datetime.date(2026, 8, 1),
            )
        self.legacy_name = self.storage.save(
            "reports/teacher_1/legacy.png",
            ContentFile(b"legacy-image-content"),
        )
        Report.all_objects.filter(pk=self.report.pk).update(image1=self.legacy_name)

    def _restore_storage(self):
        self.field.storage = self.original_storage

    def test_dry_run_does_not_copy_or_update(self):
        output = StringIO()
        call_command(
            "migrate_school_file_prefixes",
            school_id=self.school.pk,
            stdout=output,
        )

        self.report.refresh_from_db()
        self.assertEqual(self.report.image1.name, self.legacy_name)
        self.assertTrue(self.storage.exists(self.legacy_name))
        self.assertIn("يحتاج نقل 1", output.getvalue())

    def test_apply_copies_verifies_updates_and_is_resumable(self):
        output = StringIO()
        call_command(
            "migrate_school_file_prefixes",
            apply=True,
            school_id=self.school.pk,
            stdout=output,
        )

        self.report.refresh_from_db()
        migrated_name = self.report.image1.name
        self.assertTrue(migrated_name.startswith("schools/migration-school/reports/images/migrated/"))
        self.assertTrue(self.storage.exists(migrated_name))
        self.assertEqual(self.storage.size(migrated_name), self.storage.size(self.legacy_name))
        self.assertTrue(self.storage.exists(self.legacy_name), "الملف القديم يجب أن يبقى كنسخة أمان")

        second_output = StringIO()
        call_command(
            "migrate_school_file_prefixes",
            apply=True,
            school_id=self.school.pk,
            stdout=second_output,
        )
        self.report.refresh_from_db()
        self.assertEqual(self.report.image1.name, migrated_name)
        self.assertIn("داخل المسار مسبقًا 1", second_output.getvalue())
