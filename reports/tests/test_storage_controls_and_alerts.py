"""The per-teacher rate must be editable, and a filling bucket must be visible.

The rate that sizes every school's storage was only reachable through the shell,
and the manager dashboard showed nothing about storage — so a manager learned the
school was full when a teacher failed to save a report.
"""

from __future__ import annotations

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.forms import PlatformSettingsForm
from reports.models import (
    PlatformSettings,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.services_archive import school_storage_pressure

MB = 1024 * 1024


@override_settings(ALLOWED_HOSTS=["testserver"])
class PlatformStorageControlTests(TestCase):
    def setUp(self):
        self.admin = Teacher.objects.create_user(
            phone="500660001",
            name="مشرف المنصة",
            password="control-pass",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(self.admin)

    def _payload(self, **overrides):
        settings_obj = PlatformSettings.get_solo()
        payload = {
            "archive_addon_annual_price": settings_obj.archive_addon_annual_price,
            "archive_included_storage_gb": settings_obj.archive_included_storage_gb,
            "storage_mb_per_teacher": settings_obj.storage_mb_per_teacher,
            "free_storage_mb": settings_obj.free_storage_mb,
        }
        payload.update(overrides)
        return payload

    def test_the_rate_is_editable_from_the_platform_settings_form(self):
        form = PlatformSettingsForm(
            self._payload(storage_mb_per_teacher=600),
            instance=PlatformSettings.get_solo(),
        )
        self.assertTrue(form.is_valid(), form.errors)
        form.save()

        self.assertEqual(PlatformSettings.get_solo().storage_mb_per_teacher, 600)

    def test_a_value_that_would_starve_schools_is_rejected(self):
        form = PlatformSettingsForm(
            self._payload(storage_mb_per_teacher=10),
            instance=PlatformSettings.get_solo(),
        )
        self.assertFalse(form.is_valid())
        self.assertIn("storage_mb_per_teacher", form.errors)

    def test_zero_stays_allowed_as_an_explicit_opt_out(self):
        form = PlatformSettingsForm(
            self._payload(storage_mb_per_teacher=0),
            instance=PlatformSettings.get_solo(),
        )
        self.assertTrue(form.is_valid(), form.errors)

    def test_the_settings_page_shows_the_field_and_what_it_produces(self):
        response = self.client.get(reverse("reports:platform_settings"))
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("storage_mb_per_teacher", html)
        # The operator edits megabytes but thinks in "what does a 50-teacher
        # school get", so the page has to answer that.
        ladder = response.context["storage_ladder"]
        self.assertEqual([row["seats"] for row in ladder], [25, 50, 100])
        self.assertIn(ladder[1]["label"], html)

    def test_the_rate_change_reaches_schools_without_a_restart(self):
        """The rate is cached, so saving must drop the cache."""
        school = School.objects.create(name="مدرسة الضبط", code="rate-change")
        plan = SubscriptionPlan.objects.create(
            name="سعة 25", price=100, days_duration=365, max_teachers=25
        )
        SchoolSubscription.objects.create(school=school, plan=plan)
        school.refresh_from_db()

        from reports.services_archive import school_storage_limit_bytes

        settings_obj = PlatformSettings.get_solo()
        settings_obj.storage_mb_per_teacher = 400
        settings_obj.save(update_fields=["storage_mb_per_teacher"])
        self.assertEqual(school_storage_limit_bytes(school), 25 * 400 * MB)

        settings_obj.storage_mb_per_teacher = 800
        settings_obj.save(update_fields=["storage_mb_per_teacher"])
        self.assertEqual(school_storage_limit_bytes(school), 25 * 800 * MB)


@override_settings(ALLOWED_HOSTS=["testserver"])
class ManagerStorageAlertTests(TestCase):
    def setUp(self):
        settings_obj = PlatformSettings.get_solo()
        settings_obj.storage_mb_per_teacher = 400
        settings_obj.save(update_fields=["storage_mb_per_teacher"])

        self.school = School.objects.create(name="مدرسة التنبيه", code="storage-alert")
        plan = SubscriptionPlan.objects.create(
            name="سعة 25", price=100, days_duration=365, max_teachers=25
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.manager = Teacher.objects.create_user(
            phone="500660002", name="مدير التنبيه", password="alert-pass", is_staff=True
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.limit = 25 * 400 * MB

    def _use(self, fraction):
        School.objects.filter(pk=self.school.pk).update(
            storage_used_bytes=int(self.limit * fraction)
        )
        self.school.refresh_from_db()

    def _login(self):
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

    def test_a_healthy_school_sees_no_storage_noise(self):
        self._use(0.4)
        self._login()

        response = self.client.get(reverse("reports:admin_dashboard"))
        self.assertFalse(response.context["storage_pressure"]["needs_attention"])
        self.assertNotContains(response, "مساحة العمل بلغت")

    def test_the_manager_is_warned_before_uploads_stop(self):
        self._use(0.85)
        self._login()

        response = self.client.get(reverse("reports:admin_dashboard"))
        pressure = response.context["storage_pressure"]

        self.assertTrue(pressure["needs_attention"])
        self.assertEqual(pressure["warning_level"], "warning")
        self.assertContains(response, "مساحة العمل بلغت")

    def test_a_full_bucket_says_plainly_that_uploads_have_stopped(self):
        self._use(1.0)
        self._login()

        response = self.client.get(reverse("reports:admin_dashboard"))

        self.assertEqual(response.context["storage_pressure"]["warning_level"], "full")
        self.assertContains(response, "امتلأت مساحة عمل المدرسة")

    def _count_queries(self, call):
        from django.db import connection
        from django.test.utils import CaptureQueriesContext

        with CaptureQueriesContext(connection) as captured:
            call()
        return len(captured)

    def test_the_alert_is_far_cheaper_than_the_full_overview(self):
        """The dashboard loads on every visit, so it must not pay for the
        nine-way breakdown and reclaimable-year scan."""
        from reports.services_archive import school_storage_overview

        self._use(0.85)

        pressure_queries = self._count_queries(
            lambda: school_storage_pressure(self.school)
        )
        overview_queries = self._count_queries(
            lambda: school_storage_overview(self.school)
        )

        self.assertLess(
            pressure_queries,
            overview_queries,
            "مؤشر اللوحة يكلّف مثل العرض الكامل، فلا فائدة من فصله",
        )
        # A hard ceiling so it cannot creep back up unnoticed.
        self.assertLessEqual(pressure_queries, 5)

    def test_a_healthy_school_costs_even_less(self):
        """Below the threshold there is nothing to report, so it stops early."""
        self._use(0.1)
        self.assertLessEqual(
            self._count_queries(lambda: school_storage_pressure(self.school)),
            5,
        )
        self.assertFalse(school_storage_pressure(self.school)["needs_attention"])
