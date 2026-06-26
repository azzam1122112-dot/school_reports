from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    Notification,
    NotificationRecipient,
    School,
    SchoolMembership,
    Teacher,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class CircularSignatureDeadlineTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة", code="deadline-school")
        self.manager = Teacher.objects.create_user(
            phone="500700800", name="مدير", password="pass"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.user = Teacher.objects.create_user(
            phone="0512345678", name="معلم", password="pass"
        )

    def _make_circular(self, deadline):
        n = Notification.objects.create(
            title="تعميم",
            message="نص",
            requires_signature=True,
            signature_deadline_at=deadline,
            school=self.school,
            created_by=self.manager,
        )
        rec = NotificationRecipient.objects.create(notification=n, teacher=self.user)
        return n, rec

    def _sign(self, rec):
        self.client.force_login(self.user)
        return self.client.post(
            reverse("reports:circular_sign", args=[rec.pk]),
            {"phone": "0512345678", "ack": "1"},
        )

    def test_signing_blocked_after_deadline(self):
        _, rec = self._make_circular(timezone.now() - timedelta(days=1))
        self._sign(rec)
        rec.refresh_from_db()
        self.assertFalse(rec.is_signed)  # لم يُسمح بالتوقيع بعد انتهاء الموعد

    def test_signing_allowed_before_deadline(self):
        _, rec = self._make_circular(timezone.now() + timedelta(days=1))
        self._sign(rec)
        rec.refresh_from_db()
        self.assertTrue(rec.is_signed)  # التوقيع مسموح قبل الموعد

    def test_signing_allowed_without_deadline(self):
        _, rec = self._make_circular(None)
        self._sign(rec)
        rec.refresh_from_db()
        self.assertTrue(rec.is_signed)
