"""The reconciliation the data migration performs, pinned as behaviour.

Migration 0103 repairs two historical errors in one pass: document bytes that
never reached the school total, and an add-on counter that held the whole
school's usage instead of its snapshots. The migration itself runs once, so what
is pinned here is the invariant it establishes — that a full recompute lands on
the same numbers.
"""
from __future__ import annotations

from datetime import timedelta

from django.core.files.base import ContentFile
from django.test import TestCase
from django.utils import timezone

from reports.models import (
    Document,
    PlatformSettings,
    School,
    SchoolArchiveAddon,
    SchoolMembership,
    SchoolSubscription,
    SchoolYearArchive,
    SubscriptionPlan,
    Teacher,
)
from reports.services_archive import (
    recompute_school_storage,
    school_archive_overview,
    school_storage_overview,
)

MB = 1024 * 1024


class StorageBucketReconciliationTests(TestCase):
    def setUp(self):
        settings_obj = PlatformSettings.get_solo()
        settings_obj.storage_mb_per_teacher = 100
        settings_obj.free_storage_mb = 1024
        settings_obj.save(update_fields=["storage_mb_per_teacher", "free_storage_mb"])

        self.school = School.objects.create(name="مدرسة المصالحة", code="reconcile")
        plan = SubscriptionPlan.objects.create(
            name="سعة 10", price=100, days_duration=365, max_teachers=10
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.school.refresh_from_db()

        self.manager = Teacher.objects.create_user(
            phone="500770001", name="مدير", password="pass", is_staff=True
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.addon = SchoolArchiveAddon.objects.create(
            school=self.school,
            is_enabled=True,
            start_date=timezone.localdate() - timedelta(days=1),
            end_date=timezone.localdate() + timedelta(days=30),
            storage_limit_gb=10,
        )

    def _document(self, size_bytes):
        document = Document(
            school=self.school,
            title="وثيقة",
            owner=self.manager,
            uploaded_by=self.manager,
            academic_year="1447-1448",
        )
        document.file.save("d.pdf", ContentFile(b"d" * size_bytes), save=False)
        document.save()
        return document

    def _snapshot(self, size_bytes):
        archive = SchoolYearArchive(school=self.school, academic_year="1446-1447")
        archive.archive_file.save("a.zip", ContentFile(b"a" * size_bytes), save=False)
        archive.save()
        return archive

    def test_a_full_recompute_separates_the_two_buckets(self):
        self._document(2 * MB)
        self._snapshot(3 * MB)

        # Simulate a database that predates the fix: totals adrift, add-on
        # counter holding the whole school.
        School.objects.filter(pk=self.school.pk).update(storage_used_bytes=0)
        SchoolArchiveAddon.objects.filter(pk=self.addon.pk).update(
            storage_used_bytes=99 * MB
        )
        self.school.refresh_from_db()

        recompute_school_storage(self.school)
        self.school.refresh_from_db()
        self.addon.refresh_from_db()

        # Everything the school holds, in one total...
        self.assertEqual(self.school.storage_used_bytes, 5 * MB)
        # ...split correctly between the buckets that are enforced.
        self.assertEqual(school_storage_overview(self.school)["used_bytes"], 2 * MB)
        self.assertEqual(school_archive_overview(self.school)["used_bytes"], 3 * MB)
        self.assertEqual(self.addon.storage_used_bytes, 3 * MB)

    def test_the_addon_counter_maintains_itself_when_snapshots_change(self):
        """It is kept up to date where snapshots change, not on upload paths.

        It used to be refreshed after every report and achievement save — none of
        which touch snapshots — and never when a snapshot was actually created.
        """
        archive = self._snapshot(3 * MB)

        self.addon.refresh_from_db()
        self.assertEqual(self.addon.storage_used_bytes, 3 * MB)

        archive.delete()

        self.addon.refresh_from_db()
        self.assertEqual(self.addon.storage_used_bytes, 0)

    def test_uploading_work_files_leaves_the_addon_counter_alone(self):
        self._snapshot(3 * MB)
        self._document(2 * MB)

        self.addon.refresh_from_db()
        self.assertEqual(self.addon.storage_used_bytes, 3 * MB)

    def test_the_two_buckets_always_add_up_to_what_is_held(self):
        self._document(1 * MB)
        self._snapshot(4 * MB)

        overview = school_storage_overview(self.school)
        archive = school_archive_overview(self.school)

        self.assertEqual(
            overview["used_bytes"] + archive["used_bytes"],
            overview["total_held_bytes"],
            "مجموع الدلوين لا يساوي ما تحتفظ به المدرسة فعلاً",
        )
