from __future__ import annotations

from datetime import date

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    Report,
    ReportType,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
    Ticket,
)
from reports.tasks import send_password_change_email_task


@override_settings(ALLOWED_HOSTS=["testserver"])
class SecurityRegressionTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="Security School", code="security-school")
        plan = SubscriptionPlan.objects.create(
            name="Security Plan",
            price=0,
            days_duration=30,
            max_teachers=0,
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.report_type = ReportType.objects.create(
            school=self.school,
            name="Security",
            code="security",
            is_active=True,
        )
        self.user = Teacher.objects.create_user(
            phone="500010001",
            name="Current Teacher",
            password="pass",
        )
        self.other_user = Teacher.objects.create_user(
            phone="500010002",
            name="Other Teacher",
            password="pass",
        )
        SchoolMembership.objects.bulk_create(
            [
                SchoolMembership(
                    school=self.school,
                    teacher=self.user,
                    role_type=SchoolMembership.RoleType.TEACHER,
                ),
                SchoolMembership(
                    school=self.school,
                    teacher=self.other_user,
                    role_type=SchoolMembership.RoleType.TEACHER,
                ),
            ]
        )

    def _login_with_active_school(self, user=None):
        self.client.force_login(user or self.user)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

    def test_delete_report_rejects_external_next_redirect(self):
        report = Report.objects.create(
            school=self.school,
            teacher=self.user,
            teacher_name=self.user.name,
            category=self.report_type,
            title="Own report",
            report_date=date(2026, 1, 10),
        )
        self._login_with_active_school()

        response = self.client.post(
            reverse("reports:delete_my_report", args=[report.pk]),
            {"next": "https://example.invalid/phishing"},
        )

        self.assertRedirects(response, reverse("reports:my_reports"), fetch_redirect_response=False)

    def test_reports_api_does_not_expose_other_teachers_reports_to_regular_member(self):
        own_report = Report.objects.create(
            school=self.school,
            teacher=self.user,
            teacher_name=self.user.name,
            category=self.report_type,
            title="Visible own report",
            report_date=date(2026, 1, 10),
        )
        Report.objects.create(
            school=self.school,
            teacher=self.other_user,
            teacher_name=self.other_user.name,
            category=self.report_type,
            title="Hidden other report",
            report_date=date(2026, 1, 11),
        )
        self._login_with_active_school()

        response = self.client.get("/api/v1/reports/")

        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(ids, {own_report.id})

    def test_tickets_api_does_not_expose_unrelated_school_tickets_to_regular_member(self):
        own_ticket = Ticket.objects.create(
            school=self.school,
            creator=self.user,
            title="Visible own ticket",
            body="Visible",
            is_platform=False,
        )
        assigned_ticket = Ticket.objects.create(
            school=self.school,
            creator=self.other_user,
            assignee=self.user,
            title="Visible assigned ticket",
            body="Visible",
            is_platform=False,
        )
        Ticket.objects.create(
            school=self.school,
            creator=self.other_user,
            title="Hidden unrelated ticket",
            body="Hidden",
            is_platform=False,
        )
        self._login_with_active_school()

        response = self.client.get("/api/v1/tickets/")

        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()["results"]}
        self.assertEqual(ids, {own_ticket.id, assigned_ticket.id})

    def test_school_dashboard_data_endpoint_returns_tenant_payload_for_manager(self):
        manager = Teacher.objects.create_user(
            phone="500010003",
            name="School Manager",
            password="pass",
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        Report.objects.create(
            school=self.school,
            teacher=self.user,
            teacher_name=self.user.name,
            category=self.report_type,
            title="Dashboard report",
            report_date=date(2026, 1, 10),
        )
        Ticket.objects.create(
            school=self.school,
            creator=self.user,
            title="Dashboard ticket",
            body="Visible",
            is_platform=False,
        )
        self._login_with_active_school(manager)

        response = self.client.get(reverse("reports:api_admin_dashboard_data"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["period"], "all")
        self.assertEqual(payload["kpis"]["reports_count"], 1)
        self.assertEqual(payload["kpis"]["tickets_total"], 1)
        self.assertIn("reports", payload["charts"])
        self.assertIn("categories", payload["charts"])

    def test_platform_dashboard_data_endpoint_requires_superuser_and_returns_payload(self):
        superuser = Teacher.objects.create_superuser(
            phone="500010004",
            name="Platform Owner",
            password="pass",
        )
        self.client.force_login(superuser)

        response = self.client.get(reverse("reports:api_platform_dashboard_data"), {"period": "month"})

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["period"], "month")
        self.assertIn("kpis", payload)
        self.assertIn("charts", payload)

    @override_settings(
        ENV="production",
        EMAIL_BACKEND="django.core.mail.backends.smtp.EmailBackend",
        EMAIL_HOST="localhost",
        PASSWORD_CHANGE_EMAIL_ENABLED=True,
    )
    def test_password_change_email_skips_unconfigured_production_smtp(self):
        self.user.email = "teacher@example.com"
        self.user.save(update_fields=["email"])

        sent = send_password_change_email_task(self.user.id)

        self.assertIs(sent, False)
