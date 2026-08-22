from __future__ import annotations

from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import Teacher


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    EMAIL_BACKEND="reports.email_backends.ResendEmailBackend",
    RESEND_API_KEY="re_test_key_123456789",
    DEFAULT_FROM_EMAIL="no-reply@tawtheeq-ksa.com",
    PASSWORD_RESET_TIMEOUT=3600,
)
class ResendEmailBackendTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = Teacher.objects.create_user(
            phone="0558123000",
            name="مدير Resend",
            email="resend-manager@example.com",
            password="old-password",
        )

    @patch("reports.email_backends._api_request", return_value={"id": "email_123"})
    def test_django_send_mail_uses_resend_api(self, api_request):
        sent = mail.send_mail(
            "رسالة اختبار",
            "النص العادي",
            "منصة توثيق <no-reply@tawtheeq-ksa.com>",
            ["customer@example.com"],
            html_message="<p>النص العادي</p>",
        )

        self.assertEqual(sent, 1)
        self.assertEqual(api_request.call_args.args[0], "/emails")
        self.assertEqual(api_request.call_args.kwargs["method"], "POST")
        payload = api_request.call_args.kwargs["payload"]
        self.assertEqual(payload["to"], ["customer@example.com"])
        self.assertEqual(payload["subject"], "رسالة اختبار")
        self.assertEqual(payload["text"], "النص العادي")
        self.assertIn("<p>النص العادي</p>", payload["html"])
        self.assertEqual(
            payload["from"],
            "منصة توثيق <no-reply@tawtheeq-ksa.com>",
        )
        self.assertIn(
            {"name": "sender_domain", "value": "tawtheeq-ksa-com"},
            payload["tags"],
        )

    @patch("reports.email_backends._api_request", return_value={"id": "email_456"})
    def test_resend_backend_adds_platform_sender_name_to_bare_default_address(self, api_request):
        sent = mail.send_mail(
            "رسالة باسم المنصة",
            "النص العادي",
            None,
            ["customer@example.com"],
        )

        self.assertEqual(sent, 1)
        payload = api_request.call_args.kwargs["payload"]
        self.assertEqual(payload["from"], "منصة توثيق <no-reply@tawtheeq-ksa.com>")

    @patch("reports.email_backends._api_request", return_value={"id": "email_reset"})
    def test_password_reset_email_is_sent_through_resend(self, api_request):
        response = self.client.post(
            reverse("reports:password_reset"),
            {"email": "RESEND-MANAGER@example.com"},
        )

        self.assertRedirects(
            response,
            reverse("reports:password_reset_done"),
            fetch_redirect_response=False,
        )
        payload = api_request.call_args.kwargs["payload"]
        self.assertEqual(payload["to"], ["resend-manager@example.com"])
        self.assertEqual(payload["from"], "منصة توثيق <no-reply@tawtheeq-ksa.com>")
        self.assertEqual(payload["subject"], "استعادة كلمة المرور | منصة توثيق")
        self.assertIn("تعيين كلمة مرور جديدة", payload["html"])
        self.assertIn("password-reset/confirm", payload["text"])
        self.assertIn("password-reset/confirm", payload["html"])
