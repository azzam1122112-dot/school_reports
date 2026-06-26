from __future__ import annotations

from datetime import date
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import Department, Report, ReportType, School, Teacher, Ticket

from .models import SchoolYearResetJob
from .services import collect_reset_summary, execute_school_year_reset


@override_settings(ALLOWED_HOSTS=["testserver"])
class SchoolYearResetTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Reset A", code="reset-a")
        self.other_school = School.objects.create(name="Reset B", code="reset-b")
        self.teacher = Teacher.objects.create_user(
            phone="590000001",
            name="Reset Teacher",
            password="pass",
        )
        self.superuser = Teacher.objects.create_superuser(
            phone="590000099",
            name="System Admin",
            password="pass",
        )
        self.report_type = ReportType.objects.create(
            school=self.school,
            name="Activity",
            code="activity",
            is_active=True,
        )
        self.department = Department.objects.create(
            school=self.school,
            name="Activities",
            slug="activities",
        )

    def _create_report(self, school: School, title: str = "Report", image: str = "") -> Report:
        return Report.objects.create(
            school=school,
            teacher=self.teacher,
            teacher_name=self.teacher.name,
            category=self.report_type if school == self.school else None,
            title=title,
            report_date=date(2026, 1, 10),
            idea="body",
            image1=image,
        )

    def _job(self, *, school: School | None = None, include_reports: bool = True, delete_files: bool = False):
        job = SchoolYearResetJob.objects.create(
            created_by=self.superuser,
            status=SchoolYearResetJob.Status.PREVIEWED,
            include_reports=include_reports,
            include_tickets=False,
            include_achievements=False,
            include_notifications=False,
            include_share_links=False,
            delete_files=delete_files,
        )
        job.schools.set([school or self.school])
        return job

    def test_dry_run_summary_does_not_delete_records(self):
        self._create_report(self.school)
        Ticket.objects.create(school=self.school, creator=self.teacher, title="Ticket")

        summary = collect_reset_summary([self.school], {"reports": True, "tickets": True})

        self.assertEqual(summary["reports_count"], 1)
        self.assertEqual(summary["tickets_count"], 1)
        self.assertEqual(Report.objects.count(), 1)
        self.assertEqual(Ticket.objects.count(), 1)
        self.assertEqual(School.objects.count(), 2)

    def test_execute_reports_only_keeps_foundational_data(self):
        self._create_report(self.school)
        job = self._job(include_reports=True)

        execute_school_year_reset(job)

        self.assertEqual(Report.objects.filter(school=self.school).count(), 0)
        self.assertTrue(School.objects.filter(pk=self.school.pk).exists())
        self.assertTrue(Teacher.objects.filter(pk=self.teacher.pk).exists())
        self.assertTrue(Department.objects.filter(pk=self.department.pk).exists())
        self.assertTrue(ReportType.objects.filter(pk=self.report_type.pk).exists())

    def test_delete_files_uses_only_linked_target_school_files(self):
        self._create_report(self.school, title="With file", image="reports/reset-a/one.jpg")
        self._create_report(self.other_school, title="Other file", image="reports/reset-b/two.jpg")
        job = self._job(school=self.school, include_reports=True, delete_files=True)

        with patch("maintenance.services.default_storage.delete") as delete_mock:
            execute_school_year_reset(job)

        delete_mock.assert_called_once_with("reports/reset-a/one.jpg")
        self.assertFalse(Report.objects.filter(school=self.school).exists())
        self.assertTrue(Report.objects.filter(school=self.other_school).exists())

    def test_non_superuser_cannot_access_view(self):
        self.client.force_login(self.teacher)

        response = self.client.get(reverse("maintenance:school_year_reset"))

        self.assertEqual(response.status_code, 403)

    def test_superuser_view_shows_school_picker(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("maintenance:school_year_reset"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ابحث باسم المدرسة أو الكود أو المدينة")
        self.assertContains(response, reverse("maintenance:school_year_reset_school_search"))

    def test_school_search_endpoint_lists_schools_for_superuser_only(self):
        self.client.force_login(self.superuser)

        response = self.client.get(reverse("maintenance:school_year_reset_school_search"), {"q": "reset-a"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["results"][0]["code"], "reset-a")

        self.client.force_login(self.teacher)
        forbidden = self.client.get(reverse("maintenance:school_year_reset_school_search"), {"q": "reset-a"})
        self.assertEqual(forbidden.status_code, 403)

    def test_management_execute_without_confirm_fails(self):
        with self.assertRaises(CommandError):
            call_command(
                "reset_school_year",
                "--execute",
                "--school-id",
                str(self.school.id),
                stdout=StringIO(),
            )

    def test_school_id_scope_does_not_touch_other_school(self):
        self._create_report(self.school, title="Target")
        self._create_report(self.other_school, title="Safe")
        job = self._job(school=self.school, include_reports=True)

        execute_school_year_reset(job)

        self.assertEqual(Report.objects.filter(school=self.school).count(), 0)
        self.assertEqual(Report.objects.filter(school=self.other_school).count(), 1)

    def test_management_dry_run_creates_preview_job_without_deleting(self):
        self._create_report(self.school)

        call_command(
            "reset_school_year",
            "--dry-run",
            "--school-id",
            str(self.school.id),
            stdout=StringIO(),
        )

        self.assertEqual(Report.objects.filter(school=self.school).count(), 1)
        self.assertTrue(SchoolYearResetJob.objects.filter(status=SchoolYearResetJob.Status.PREVIEWED).exists())
