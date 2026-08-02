from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    Notification,
    NotificationRecipient,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class CircularSignatureDeadlineTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة", code="deadline-school")
        plan = SubscriptionPlan.objects.create(
            name="خطة اختبار التعاميم",
            price=0,
            days_duration=30,
            max_teachers=0,
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.manager = Teacher.objects.create_user(
            phone="500700800", name="مدير", password="pass", is_staff=True
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

    def test_recipient_detail_renders_official_document_and_print_action(self):
        notification, rec = self._make_circular(timezone.now() + timedelta(days=1))
        self.client.force_login(self.user)

        response = self.client.get(reverse("reports:my_circular_detail", args=[rec.pk]))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "reports/my_circular_detail.html")
        self.assertContains(response, notification.title)
        self.assertContains(response, "وثيقة إدارية رسمية")
        self.assertContains(response, "طباعة التعميم")
        self.assertContains(response, "اعتماد التوقيع نهائيًا")
        self.assertContains(response, "CIR-")

    def test_manager_detail_and_print_report_use_real_signature_percentage(self):
        notification, signed_recipient = self._make_circular(timezone.now() + timedelta(days=1))
        signed_recipient.is_read = True
        signed_recipient.is_signed = True
        signed_recipient.read_at = timezone.now()
        signed_recipient.signed_at = timezone.now()
        signed_recipient.save(update_fields=["is_read", "is_signed", "read_at", "signed_at"])
        second_user = Teacher.objects.create_user(
            phone="0523456789", name="معلم ثان", password="pass"
        )
        NotificationRecipient.objects.create(notification=notification, teacher=second_user)

        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

        detail_response = self.client.get(
            reverse("reports:notification_detail", args=[notification.pk])
        )
        print_response = self.client.get(
            reverse("reports:notification_signatures_print", args=[notification.pk])
        )

        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.context["signature_stats"]["signed_percentage"], 50)
        self.assertContains(detail_response, "نسبة اكتمال التوقيع")
        self.assertEqual(print_response.status_code, 200)
        self.assertEqual(print_response.context["stats"]["signed_percentage"], 50)
        self.assertContains(print_response, "تقرير الاطلاع والتوقيع")
        self.assertContains(print_response, "50%")
        self.assertNotContains(print_response, "صفحة 1 من 1")
        self.assertNotContains(print_response, "cdnjs.cloudflare.com")
