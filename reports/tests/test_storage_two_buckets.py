"""Daily work and yearly snapshots hold separate storage budgets.

They used to share one pool, which meant a once-a-year administrative action —
creating a snapshot — could exhaust the space that gates every teacher upload and
freeze the whole school. These tests pin the separation, and the release valve
that keeps the snapshot bucket from becoming a dead end.
"""

from __future__ import annotations

from datetime import timedelta

from django.core.files.base import ContentFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    AuditLog,
    PlatformSettings,
    SchoolArchiveAddon,
    School,
    SchoolMembership,
    SchoolSubscription,
    SchoolYearArchive,
    SubscriptionPlan,
    Teacher,
)
from reports.services_archive import (
    archive_snapshot_capacity_error,
    archive_storage_capacity_error,
    attach_school_consumption_rows,
    school_archive_overview,
    school_storage_limit_bytes,
    school_storage_overview,
    school_work_used_bytes,
    storage_bytes_for_seats,
    sync_school_archive_storage_usage,
)

MB = 1024 * 1024
GB = 1024 * MB


class _Upload:
    def __init__(self, size):
        self.size = size
        self.name = "upload.bin"


@override_settings(ALLOWED_HOSTS=["testserver"])
class TwoStorageBucketsTests(TestCase):
    def setUp(self):
        settings_obj = PlatformSettings.get_solo()
        settings_obj.storage_mb_per_teacher = 400
        settings_obj.free_storage_mb = 1024
        settings_obj.save(
            update_fields=["storage_mb_per_teacher", "free_storage_mb"]
        )
        self.school = School.objects.create(name="مدرسة الدلوين", code="two-buckets")
        self.manager = Teacher.objects.create_user(
            phone="500550001", name="مدير الدلوين", password="bucket-pass", is_staff=True
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

    def _archive_addon(self, *, limit_gb=50, active=True):
        """Archiving is a separate purchase; without it there is no archive space."""
        return SchoolArchiveAddon.objects.create(
            school=self.school,
            is_enabled=True,
            start_date=timezone.localdate() - timedelta(days=30),
            end_date=(
                timezone.localdate() + timedelta(days=30)
                if active
                else timezone.localdate() - timedelta(days=1)
            ),
            storage_limit_gb=limit_gb,
        )

    def _subscribe(self, seats=50):
        plan = SubscriptionPlan.objects.create(
            name=f"سعة {seats}", price=100, days_duration=365, max_teachers=seats
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.school.refresh_from_db()

    def _snapshot(self, *, year, size_bytes):
        """Snapshots are immutable once written, so the file goes in at create."""
        archive = SchoolYearArchive(
            school=self.school,
            academic_year=year,
            storage_bytes=size_bytes,
        )
        archive.archive_file.save(
            f"archive-{year}.zip", ContentFile(b"x"), save=False
        )
        archive.save()
        SchoolYearArchive.objects.filter(pk=archive.pk).update(storage_bytes=size_bytes)
        return SchoolYearArchive.objects.get(pk=archive.pk)

    def _total_used(self):
        return int(
            School.objects.filter(pk=self.school.pk)
            .values_list("storage_used_bytes", flat=True)
            .first()
            or 0
        )

    def _set_total_used(self, value):
        School.objects.filter(pk=self.school.pk).update(storage_used_bytes=value)
        self.school.refresh_from_db()

    def _login(self):
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

    # ------------------------------------------------------------ the buckets

    def test_the_two_buckets_come_from_two_different_purchases(self):
        self._subscribe(seats=50)
        self._archive_addon(limit_gb=50)

        # Work space follows the teacher capacity...
        self.assertEqual(school_storage_limit_bytes(self.school), 50 * 400 * MB)
        # ...archive space follows the archive service, not the teacher count.
        self.assertEqual(school_archive_overview(self.school)["limit_bytes"], 50 * GB)

    def test_a_school_without_the_archive_service_has_no_archive_space(self):
        self._subscribe(seats=50)

        overview = school_archive_overview(self.school)
        self.assertFalse(overview["is_subscribed"])
        self.assertEqual(overview["limit_bytes"], 0)
        # And its work space is untouched by that.
        self.assertEqual(school_storage_limit_bytes(self.school), 50 * 400 * MB)

    def test_an_unsubscribed_school_is_told_to_activate_the_service(self):
        self._subscribe(seats=50)

        message = archive_snapshot_capacity_error(self.school, 1 * GB)
        self.assertIn("خدمة الأرشيف السنوي غير مفعّلة", message)

    def test_a_lapsed_archive_service_stops_new_snapshots(self):
        self._subscribe(seats=50)
        self._archive_addon(limit_gb=50, active=False)

        self.assertFalse(school_archive_overview(self.school)["is_subscribed"])
        self.assertIn(
            "خدمة الأرشيف السنوي غير مفعّلة",
            archive_snapshot_capacity_error(self.school, 1 * GB),
        )
        # Saved snapshots and daily work are both unaffected.
        self.assertEqual(
            archive_storage_capacity_error(self.school, [_Upload(2 * MB)]), ""
        )

    def test_work_usage_excludes_saved_snapshots(self):
        self._subscribe(seats=50)
        self._archive_addon(limit_gb=50)
        self._snapshot(year="1446-1447", size_bytes=3 * GB)
        self._set_total_used(5 * GB)  # 2GB of live work + the 3GB snapshot

        self.assertEqual(school_work_used_bytes(self.school), 2 * GB)

    # ------------------------------------------------------ what the manager sees

    def test_every_screen_quotes_the_same_number_as_the_gate(self):
        """The card, the banner and the gate used to disagree.

        The card read the school's grand total, so a school that archived a year
        was shown as consuming working space it had not spent — and only schools
        that bought archiving were ever shown that inflated figure.
        """
        self._subscribe(seats=50)
        self._archive_addon(limit_gb=50)
        self._snapshot(year="1446-1447", size_bytes=3 * GB)
        self._set_total_used(5 * GB)  # 2GB live work + 3GB snapshot

        overview = school_storage_overview(self.school)

        self.assertEqual(overview["used_bytes"], 2 * GB)
        self.assertEqual(overview["used_bytes"], school_work_used_bytes(self.school))
        self.assertEqual(
            overview["remaining_bytes"],
            school_storage_limit_bytes(self.school) - 2 * GB,
        )
        # The snapshot space is still reported — under the bucket that holds it.
        self.assertEqual(overview["snapshot_bytes"], 3 * GB)
        self.assertEqual(school_archive_overview(self.school)["used_bytes"], 3 * GB)
        self.assertEqual(overview["total_held_bytes"], 5 * GB)

    def test_archiving_a_year_never_moves_the_work_card(self):
        """Two identical schools; only one bought archiving. Same work reading."""
        self._subscribe(seats=25)
        self._archive_addon(limit_gb=50)
        other = School.objects.create(name="مدرسة بلا أرشفة", code="no-addon")
        SchoolSubscription.objects.create(school=other, plan=self.school.subscription.plan)
        other.refresh_from_db()

        self._snapshot(year="1446-1447", size_bytes=4 * GB)

        self.assertEqual(
            school_storage_overview(self.school)["used_bytes"],
            school_storage_overview(other)["used_bytes"],
        )
        self.assertEqual(school_storage_overview(self.school)["usage_percent"], 0)

    def test_the_school_list_measures_work_the_same_way(self):
        """The platform directory and the manager's own card must not diverge."""
        self._subscribe(seats=50)
        self._archive_addon(limit_gb=50)
        self._snapshot(year="1446-1447", size_bytes=3 * GB)
        self._set_total_used(5 * GB)

        schools = [School.objects.get(pk=self.school.pk)]
        attach_school_consumption_rows(schools)
        row = schools[0].consumption_row

        self.assertEqual(row["storage"]["used"], 2 * GB)
        self.assertEqual(row["archive"]["used"], 3 * GB)

    def test_the_addon_counter_measures_snapshots_not_the_whole_school(self):
        """``SchoolArchiveAddon.storage_used_bytes`` is shown to the operator
        against the add-on's own GB limit, so it must hold snapshot bytes."""
        self._subscribe(seats=50)
        addon = self._archive_addon(limit_gb=50)
        self._snapshot(year="1446-1447", size_bytes=1 * GB)
        self._set_total_used(9 * GB)  # 8GB of live work on top of the snapshot

        sync_school_archive_storage_usage(self.school)
        addon.refresh_from_db()

        self.assertEqual(addon.storage_used_bytes, 1 * GB)
        self.assertEqual(addon.storage_used_gb, 1.0)

    # -------------------------------------------------------------- the gates

    def test_a_full_archive_does_not_stop_teachers_from_uploading(self):
        """The whole point: an archive run must never freeze the school."""
        self._subscribe(seats=25)
        self._archive_addon(limit_gb=10)
        archive_limit = 10 * GB
        self._snapshot(year="1446-1447", size_bytes=archive_limit)
        self._set_total_used(archive_limit + 1 * GB)

        # Snapshot bucket is full...
        self.assertTrue(archive_snapshot_capacity_error(self.school, 1 * GB))
        # ...but daily work carries on.
        self.assertEqual(
            archive_storage_capacity_error(self.school, [_Upload(2 * MB)]),
            "",
            "امتلاء الأرشيف أوقف رفع المعلمين",
        )

    def test_a_full_work_bucket_still_blocks_uploads(self):
        self._subscribe(seats=25)
        self._set_total_used(storage_bytes_for_seats(25))

        self.assertNotEqual(
            archive_storage_capacity_error(self.school, [_Upload(50 * MB)]), ""
        )

    def test_the_archive_gate_explains_the_way_out(self):
        self._subscribe(seats=25)
        self._archive_addon(limit_gb=10)
        self._snapshot(year="1446-1447", size_bytes=10 * GB)

        message = archive_snapshot_capacity_error(self.school, 1 * GB)
        self.assertIn("نزّل نسخة سنة سابقة", message)
        self.assertIn("لا يؤثر ذلك على عمل المعلمين", message)

    # ------------------------------------------------------- the release valve

    def test_the_manager_can_free_archive_space_by_deleting_a_snapshot(self):
        self._subscribe(seats=25)
        self._archive_addon(limit_gb=50)
        archive = self._snapshot(year="1446-1447", size_bytes=2 * GB)
        self._login()

        before = school_archive_overview(self.school)["used_bytes"]
        self.assertEqual(before, 2 * GB)

        response = self.client.post(
            reverse("reports:school_archive_delete", args=[archive.pk]),
            {"confirm_year": "1446-1447"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SchoolYearArchive.objects.filter(pk=archive.pk).exists())
        self.assertEqual(school_archive_overview(self.school)["used_bytes"], 0)

    def test_deleting_a_snapshot_requires_typing_the_year_back(self):
        self._subscribe(seats=25)
        self._archive_addon(limit_gb=50)
        archive = self._snapshot(year="1446-1447", size_bytes=1 * GB)
        self._login()

        response = self.client.post(
            reverse("reports:school_archive_delete", args=[archive.pk]),
            {"confirm_year": "wrong"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(SchoolYearArchive.objects.filter(pk=archive.pk).exists())

    def test_deleting_a_snapshot_is_written_to_the_audit_log(self):
        self._subscribe(seats=25)
        self._archive_addon(limit_gb=50)
        archive = self._snapshot(year="1446-1447", size_bytes=1 * GB)
        self._login()

        self.client.post(
            reverse("reports:school_archive_delete", args=[archive.pk]),
            {"confirm_year": "1446-1447"},
        )

        entry = AuditLog.objects.filter(model_name="SchoolYearArchive").first()
        self.assertIsNotNone(entry, "حذف نسخة الأرشيف لم يُسجَّل في سجل العمليات")
        self.assertEqual(entry.teacher, self.manager)

    def test_a_teacher_cannot_destroy_a_snapshot(self):
        self._subscribe(seats=25)
        self._archive_addon(limit_gb=50)
        archive = self._snapshot(year="1446-1447", size_bytes=1 * GB)
        teacher = Teacher.objects.create_user(
            phone="500550002", name="معلم", password="bucket-pass"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.client.force_login(teacher)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        self.client.post(
            reverse("reports:school_archive_delete", args=[archive.pk]),
            {"confirm_year": "1446-1447"},
        )

        self.assertTrue(SchoolYearArchive.objects.filter(pk=archive.pk).exists())

    def test_deleting_is_never_reachable_by_a_plain_get(self):
        self._subscribe(seats=25)
        self._archive_addon(limit_gb=50)
        archive = self._snapshot(year="1446-1447", size_bytes=1 * GB)
        self._login()

        response = self.client.get(
            reverse("reports:school_archive_delete", args=[archive.pk])
        )

        self.assertEqual(response.status_code, 405)
        self.assertTrue(SchoolYearArchive.objects.filter(pk=archive.pk).exists())
