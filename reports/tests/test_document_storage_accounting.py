"""Documents are gated by the work bucket, so they must be counted in it.

The document archive checked every upload against the school's storage limit but
never added a byte to it: ``Document`` was missing from the tracking signals and
from the reconciliation scan. Schools that used the feature therefore consumed
real space invisibly — and a backfill would have erased even that from the books.
"""
from __future__ import annotations

from django.core.files.base import ContentFile
from django.test import TestCase

from reports.models import (
    Document,
    PlatformSettings,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.services_archive import (
    archive_storage_capacity_error,
    calculate_school_archive_storage_bytes,
    recompute_school_storage,
    school_storage_breakdown,
    school_storage_overview,
)

MB = 1024 * 1024


class _Upload:
    def __init__(self, size):
        self.size = size
        self.name = "upload.pdf"


class DocumentStorageAccountingTests(TestCase):
    def setUp(self):
        settings_obj = PlatformSettings.get_solo()
        settings_obj.storage_mb_per_teacher = 1
        settings_obj.free_storage_mb = 10
        settings_obj.save(update_fields=["storage_mb_per_teacher", "free_storage_mb"])

        self.school = School.objects.create(name="مدرسة الوثائق", code="docs-school")
        plan = SubscriptionPlan.objects.create(
            name="سعة 10", price=100, days_duration=365, max_teachers=10
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.school.refresh_from_db()

        self.manager = Teacher.objects.create_user(
            phone="500660001", name="مدير الوثائق", password="docs-pass", is_staff=True
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

    def _document(self, size_bytes, *, title="وثيقة"):
        document = Document(
            school=self.school,
            title=title,
            owner=self.manager,
            uploaded_by=self.manager,
            academic_year="1447-1448",
        )
        document.file.save(
            f"{title}.pdf", ContentFile(b"d" * size_bytes), save=False
        )
        document.save()
        return document

    def test_uploading_a_document_charges_the_school(self):
        document = self._document(2 * MB)

        self.school.refresh_from_db()
        self.assertEqual(document.storage_bytes, 2 * MB)
        self.assertEqual(self.school.storage_used_bytes, 2 * MB)

    def test_documents_appear_in_the_work_breakdown(self):
        self._document(3 * MB)

        overview = school_storage_overview(self.school)

        self.assertEqual(school_storage_breakdown(self.school)["documents"], 3 * MB)
        self.assertEqual(overview["breakdown"]["documents"]["bytes"], 3 * MB)
        self.assertEqual(overview["used_bytes"], 3 * MB)

    def test_deleting_a_document_gives_the_space_back(self):
        document = self._document(4 * MB)

        document.delete()

        self.school.refresh_from_db()
        self.assertEqual(self.school.storage_used_bytes, 0)

    def test_the_reconciliation_scan_keeps_documents(self):
        """A backfill used to wipe document bytes from the school total."""
        self._document(5 * MB)

        self.assertEqual(calculate_school_archive_storage_bytes(self.school), 5 * MB)
        self.assertEqual(recompute_school_storage(self.school), 5 * MB)
        self.school.refresh_from_db()
        self.assertEqual(self.school.storage_used_bytes, 5 * MB)

    def test_documents_fill_the_bucket_they_are_gated_against(self):
        """The gate refused oversized documents while pretending none existed."""
        self._document(9 * MB)  # limit is 10 seats × 1MB

        self.assertNotEqual(
            archive_storage_capacity_error(self.school, [_Upload(2 * MB)]),
            "",
            "الوثائق المرفوعة لم تُحتسب في الحد الذي تُفحص أمامه",
        )

    def test_editing_a_document_without_touching_the_file_costs_nothing(self):
        document = self._document(2 * MB)

        document.title = "عنوان جديد"
        document.save(update_fields=["title", "updated_at"])

        self.school.refresh_from_db()
        self.assertEqual(self.school.storage_used_bytes, 2 * MB)
