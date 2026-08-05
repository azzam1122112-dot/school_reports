from django.core import mail
from django.test import TestCase, override_settings

from reports.models import School, SchoolMembership, Teacher
from reports.tasks import _daily_summary_for_school


@override_settings(
    DAILY_MANAGER_REPORT_INAPP_ENABLED=False,
    DAILY_MANAGER_REPORT_EMAIL_ENABLED=True,
    DAILY_MANAGER_REPORT_WHATSAPP_ENABLED=False,
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
    SITE_URL="https://tawtheeq.example",
)
class WeeklyManagerSummaryTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة الملخص", code="weekly-summary-school")
        self.manager = Teacher.objects.create_user(
            phone="500020001",
            name="مدير الملخص",
            email="manager@example.com",
            password="pass12345",
            is_staff=True,
        )
        self.membership = SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
            weekly_summary_email_enabled=True,
        )

    def test_daily_summary_sends_email_when_manager_opted_in(self):
        mail.outbox.clear()

        result = _daily_summary_for_school(self.school.id)

        self.assertTrue(result["processed"])
        self.assertEqual(result["emails_sent"], 1)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["manager@example.com"])
        self.assertEqual(len(mail.outbox[0].alternatives), 1)
        html, content_type = mail.outbox[0].alternatives[0]
        self.assertEqual(content_type, "text/html")
        self.assertIn("منصة توثيق", html)
        self.assertIn("مدرسة الملخص", html)
        self.assertIn("مدير الملخص", html)
        self.assertIn("https://tawtheeq.example/static/img/logo1.png", html)
        self.assertIn("التقارير المنجزة", html)
        self.assertIn("البلاغات المفتوحة", html)
        self.assertIn("البلاغات المغلقة", html)
        self.assertIn(f"/staff/schools/{self.school.id}/profile/", html)

    def test_daily_summary_skips_email_when_manager_opted_out(self):
        self.membership.weekly_summary_email_enabled = False
        self.membership.save(update_fields=["weekly_summary_email_enabled"])
        mail.outbox.clear()

        result = _daily_summary_for_school(self.school.id)

        self.assertTrue(result["processed"])
        self.assertEqual(result["emails_sent"], 0)
        self.assertEqual(len(mail.outbox), 0)
