"""The expiry reminder is the one notice a manager cannot afford to miss.

When it lapses the service stops, so the reminder goes out on both channels:
the in-app notification and the manager's inbox.
"""

from __future__ import annotations

from datetime import timedelta

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.utils import timezone

from reports.models import (
    Notification,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.tasks import check_subscription_expiry_task


@override_settings(
    SUBSCRIPTION_EXPIRY_REMINDER_ENABLED=True,
    SUBSCRIPTION_EXPIRY_REMINDER_EMAIL_ENABLED=True,
    SUBSCRIPTION_EXPIRY_REMINDER_DAYS=[14, 7, 3, 1],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@example.com",
)
class SubscriptionExpiryReminderEmailTests(TestCase):
    def setUp(self):
        # The task guards itself with a 5-minute cache lock that it never
        # releases; without clearing it every test after the first is skipped.
        cache.clear()
        self.school = School.objects.create(name="مدرسة الاشتراك", code="expiry-school")
        self.plan = SubscriptionPlan.objects.create(
            name="الباقة السنوية", price=0, days_duration=365, max_teachers=0
        )
        self.manager = Teacher.objects.create_user(
            phone="500440001",
            name="مدير الاشتراك",
            email="principal@example.com",
            password="expiry-pass",
            is_staff=True,
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )

    def _subscription_expiring_in(self, days: int) -> SchoolSubscription:
        sub = SchoolSubscription.objects.filter(school=self.school).first()
        end_date = timezone.localdate() + timedelta(days=days)
        if sub is None:
            sub = SchoolSubscription.objects.create(school=self.school, plan=self.plan)
        SchoolSubscription.objects.filter(pk=sub.pk).update(
            end_date=end_date, is_active=True, canceled_at=None
        )
        sub.refresh_from_db()
        return sub

    def test_reminder_emails_the_manager_on_a_reminder_day(self):
        self._subscription_expiring_in(7)
        mail.outbox.clear()

        summary = check_subscription_expiry_task()

        self.assertEqual(summary["reminders_sent"], 1)
        self.assertEqual(summary["emails_sent"], 1)
        self.assertEqual(len(mail.outbox), 1)
        sent = mail.outbox[0]
        self.assertEqual(sent.to, ["principal@example.com"])
        self.assertIn("مدرسة الاشتراك", sent.subject)
        self.assertIn("7", sent.subject)
        self.assertIn("منصة توثيق", sent.subject)
        self.assertIn("الباقة السنوية", sent.body)
        self.assertEqual(len(sent.alternatives), 1)
        html, content_type = sent.alternatives[0]
        self.assertEqual(content_type, "text/html")
        self.assertIn("تنبيه الاشتراك", html)
        self.assertIn("مدرسة الاشتراك", html)
        self.assertIn("الباقة السنوية", html)
        self.assertIn("إدارة الاشتراك والتجديد", html)
        self.assertIn("توثيق أدق، متابعة أوضح", html)

    def test_reminder_is_not_emailed_twice_within_a_day(self):
        self._subscription_expiring_in(3)
        check_subscription_expiry_task()
        mail.outbox.clear()
        cache.clear()  # drop the run lock, not the 24h notification de-dup

        summary = check_subscription_expiry_task()

        self.assertEqual(summary["skipped_duplicate"], 1)
        self.assertEqual(mail.outbox, [])

    def test_no_reminder_outside_the_configured_days(self):
        self._subscription_expiring_in(9)
        mail.outbox.clear()

        summary = check_subscription_expiry_task()

        self.assertEqual(summary["reminders_sent"], 0)
        self.assertEqual(mail.outbox, [])
        self.assertFalse(Notification.objects.filter(school=self.school).exists())

    @override_settings(SUBSCRIPTION_EXPIRY_REMINDER_EMAIL_ENABLED=False)
    def test_in_app_notice_still_fires_when_the_email_channel_is_off(self):
        self._subscription_expiring_in(1)
        mail.outbox.clear()

        summary = check_subscription_expiry_task()

        self.assertEqual(summary["reminders_sent"], 1)
        self.assertEqual(summary["emails_sent"], 0)
        self.assertEqual(mail.outbox, [])
        self.assertTrue(Notification.objects.filter(school=self.school).exists())
