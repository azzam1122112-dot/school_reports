from __future__ import annotations

from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from reports.models import (
    Payment,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.tasks import send_subscription_activation_email_task


class SystemEmailProbeTests(SimpleTestCase):
    @override_settings(EMAIL_BACKEND="django.core.mail.backends.console.EmailBackend")
    def test_probe_refuses_a_non_delivery_backend(self):
        with self.assertRaisesMessage(CommandError, "real Resend or SMTP backend"):
            call_command("send_system_email_probe", "operator@example.com")

    @override_settings(
        EMAIL_BACKEND="reports.email_backends.ResendEmailBackend",
        RESEND_API_KEY="re_test_key_123456789",
        DEFAULT_FROM_EMAIL="no-reply@tawtheeq-ksa.com",
    )
    @patch("reports.email_backends._api_request", return_value={"id": "probe_123"})
    def test_probe_uses_the_real_system_backend_path(self, api_request):
        output = StringIO()

        call_command("send_system_email_probe", "operator@example.com", stdout=output)

        payload = api_request.call_args.kwargs["payload"]
        self.assertEqual(payload["to"], ["operator@example.com"])
        self.assertIn("اختبار جاهزية البريد", payload["subject"])
        self.assertIn("قناة بريد النظام", payload["html"])
        self.assertIn("accepted by the configured provider", output.getvalue())


@override_settings(
    EMAIL_BACKEND="reports.email_backends.ResendEmailBackend",
    RESEND_API_KEY="re_test_key_123456789",
    DEFAULT_FROM_EMAIL="no-reply@tawtheeq-ksa.com",
    SITE_URL="https://tawtheeq-ksa.com",
    SUBSCRIPTION_ACTIVATION_EMAIL_ENABLED=True,
)
class SubscriptionActivationEmailTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة التفعيل", code="activation-school")
        self.plan = SubscriptionPlan.objects.create(
            name="الباقة السنوية",
            price="1290.00",
            days_duration=365,
            max_teachers=25,
        )
        self.subscription = SchoolSubscription.objects.create(
            school=self.school,
            plan=self.plan,
        )
        self.manager = Teacher.objects.create_user(
            phone="0558111000",
            name="مدير التفعيل",
            email="activation-manager@example.com",
            password="safe-password",
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.payment = Payment.objects.create(
            school=self.school,
            requested_plan=self.plan,
            subscription=self.subscription,
            amount="1290.00",
            purpose=Payment.Purpose.SUBSCRIPTION,
            status=Payment.Status.APPROVED,
            effects_applied_at=timezone.now(),
            created_by=self.manager,
        )

    @patch("reports.email_backends._api_request", return_value={"id": "activation_123"})
    def test_approved_subscription_sends_full_details_through_resend(self, api_request):
        summary = send_subscription_activation_email_task(self.payment.pk)

        self.assertEqual(summary, {"sent": 1, "skipped": 0, "failed": 0})
        payload = api_request.call_args.kwargs["payload"]
        self.assertEqual(payload["to"], ["activation-manager@example.com"])
        self.assertIn("مدرسة التفعيل", payload["subject"])
        self.assertIn("الباقة السنوية", payload["text"])
        self.assertIn("1290.00", payload["html"])
        self.assertIn("subscription/invoice", payload["html"])

    @patch("reports.email_backends._api_request")
    def test_unapplied_payment_never_sends_activation_email(self, api_request):
        self.payment.effects_applied_at = None
        self.payment.save(update_fields=["effects_applied_at"])

        summary = send_subscription_activation_email_task(self.payment.pk)

        self.assertEqual(summary["skipped"], 1)
        api_request.assert_not_called()
