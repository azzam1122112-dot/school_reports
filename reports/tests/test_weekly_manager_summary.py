from django.core import mail
from django.test import TestCase, override_settings

from reports.models import (
    Notification,
    NotificationRecipient,
    School,
    SchoolMembership,
    Teacher,
)
from reports.tasks import _daily_summary_for_school


@override_settings(
    DAILY_MANAGER_REPORT_INAPP_ENABLED=True,
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
        )

    def test_daily_summary_notifies_manager_in_app_only(self):
        mail.outbox.clear()

        result = _daily_summary_for_school(self.school.id)

        self.assertTrue(result["processed"])
        self.assertEqual(result["inapp_sent"], 1)
        self.assertNotIn("emails_sent", result)

        notification = Notification.objects.filter(school=self.school).latest("id")
        self.assertIn("الملخص الأسبوعي", notification.title)
        self.assertIn("مدرسة الملخص", notification.message)
        self.assertTrue(
            NotificationRecipient.objects.filter(
                notification=notification, teacher=self.manager
            ).exists()
        )

    def test_daily_summary_never_sends_email(self):
        """The summary is a dashboard item; a manager with a valid email gets none."""
        mail.outbox.clear()

        _daily_summary_for_school(self.school.id)

        self.assertEqual(mail.outbox, [])
