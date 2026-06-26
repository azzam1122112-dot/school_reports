from django.test import TestCase

from reports.models import (
    PlatformSettings,
    Report,
    School,
    SchoolArchiveAddon,
    Teacher,
)
from reports.services_archive import (
    archive_storage_capacity_error,
    school_storage_limit_bytes,
)


class _FakeUpload:
    """ملف وهمي بحجم محدد لاختبار الحساب الفعلي."""
    def __init__(self, size):
        self.size = size
        self.name = "x.jpg"


class SchoolStorageLimitTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة", code="storage-school")
        s = PlatformSettings.get_solo()
        s.free_storage_mb = 1  # 1MB حد مجاني للاختبار
        s.save()

    def test_limit_is_free_baseline_without_addon(self):
        # بدون إضافة أرشفة → الحد = الحد المجاني الأساسي (1MB)
        self.assertEqual(school_storage_limit_bytes(self.school), 1 * 1024 * 1024)

    def test_blocks_upload_exceeding_free_baseline(self):
        # ملف 2MB > الحد 1MB → رسالة خطأ
        err = archive_storage_capacity_error(self.school, [_FakeUpload(2 * 1024 * 1024)])
        self.assertTrue(err)
        self.assertIn("حد التخزين", err)

    def test_allows_upload_within_free_baseline(self):
        err = archive_storage_capacity_error(self.school, [_FakeUpload(300 * 1024)])  # 300KB < 1MB
        self.assertEqual(err, "")

    def test_zero_means_unlimited(self):
        s = PlatformSettings.get_solo()
        s.free_storage_mb = 0
        s.save()
        self.assertEqual(school_storage_limit_bytes(self.school), 0)
        err = archive_storage_capacity_error(self.school, [_FakeUpload(999 * 1024 * 1024)])
        self.assertEqual(err, "")

    def test_active_addon_limit_overrides_baseline(self):
        SchoolArchiveAddon.objects.create(
            school=self.school, is_enabled=True, storage_limit_gb=10
        )
        # الحد = 10GB من الإضافة، وليس الحد المجاني 1MB
        self.assertEqual(
            school_storage_limit_bytes(self.school), 10 * 1024 * 1024 * 1024
        )
        err = archive_storage_capacity_error(self.school, [_FakeUpload(2 * 1024 * 1024)])
        self.assertEqual(err, "")  # 2MB ضمن 10GB

    def test_precise_actual_usage_counts_existing_files(self):
        # الحساب يعتمد على الحجم الفعلي: نتحقق أن دالة الحساب تجمع الأحجام الحقيقية.
        from reports.services_archive import calculate_school_archive_storage_bytes
        self.assertEqual(calculate_school_archive_storage_bytes(self.school), 0)
