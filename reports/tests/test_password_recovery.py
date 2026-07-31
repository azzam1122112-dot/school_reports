import re
from urllib.parse import urlsplit
from unittest.mock import patch

from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import Teacher


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend",
    DEFAULT_FROM_EMAIL="no-reply@tawtheeq-ksa.com",
    PASSWORD_RESET_TIMEOUT=3600,
)
class PasswordRecoveryTests(TestCase):
    def setUp(self):
        self.user = Teacher.objects.create_user(
            phone="0558000001",
            name="مستخدم الاستعادة",
            password="Old-safe-password",
            email="recovery@example.com",
        )

    def _request_reset(self, email: str):
        return self.client.post(
            reverse("reports:password_reset"),
            {"email": email},
        )

    def _reset_path_from_email(self) -> str:
        match = re.search(r"https?://[^\s]+", mail.outbox[0].body)
        self.assertIsNotNone(match)
        return urlsplit(match.group(0)).path

    def test_login_links_to_password_recovery_form(self):
        response = self.client.get(reverse("reports:login"))

        self.assertContains(
            response,
            f'href="{reverse("reports:password_reset")}"',
        )
        self.assertNotContains(response, "يرجى التواصل مع إدارة المدرسة")

    def test_registered_email_receives_one_time_reset_link(self):
        response = self._request_reset("RECOVERY@example.com")

        self.assertRedirects(
            response,
            reverse("reports:password_reset_done"),
            fetch_redirect_response=False,
        )
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(
            mail.outbox[0].subject,
            "استعادة كلمة المرور في منصة توثيق",
        )
        self.assertIn(
            reverse(
                "reports:password_reset_confirm",
                kwargs={"uidb64": "placeholder", "token": "placeholder"},
            ).rsplit("/", 3)[0],
            mail.outbox[0].body,
        )

    def test_unknown_email_has_same_public_result_without_sending(self):
        response = self._request_reset("unknown@example.com")

        self.assertRedirects(
            response,
            reverse("reports:password_reset_done"),
            fetch_redirect_response=False,
        )
        self.assertEqual(len(mail.outbox), 0)

        done_response = self.client.get(reverse("reports:password_reset_done"))
        self.assertContains(done_response, "إذا كان البريد مرتبطًا بحساب نشط")
        self.assertNotContains(done_response, "unknown@example.com")

    def test_duplicate_email_is_not_used_for_self_service_recovery(self):
        Teacher.objects.create_user(
            phone="0558000002",
            name="حساب قديم ببريد مكرر",
            password="Another-safe-password",
            email=self.user.email,
        )

        with self.assertLogs("reports.forms", level="WARNING"):
            response = self._request_reset(self.user.email)

        self.assertRedirects(
            response,
            reverse("reports:password_reset_done"),
            fetch_redirect_response=False,
        )
        self.assertEqual(len(mail.outbox), 0)

    def test_delivery_failure_does_not_reveal_registered_account(self):
        with patch(
            "django.core.mail.EmailMultiAlternatives.send",
            side_effect=RuntimeError("smtp unavailable"),
        ), self.assertLogs("django.contrib.auth", level="ERROR"):
            response = self._request_reset(self.user.email)

        self.assertRedirects(
            response,
            reverse("reports:password_reset_done"),
            fetch_redirect_response=False,
        )

    def test_valid_link_changes_password_and_cannot_be_reused(self):
        self._request_reset(self.user.email)
        original_reset_path = self._reset_path_from_email()

        confirm_response = self.client.get(original_reset_path)
        self.assertEqual(confirm_response.status_code, 302)
        set_password_path = confirm_response["Location"]

        response = self.client.post(
            set_password_path,
            {
                "new_password1": "Cedar!River-4827",
                "new_password2": "Cedar!River-4827",
            },
        )

        self.assertRedirects(
            response,
            reverse("reports:password_reset_complete"),
            fetch_redirect_response=False,
        )
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password("Cedar!River-4827"))

        reused_response = self.client.get(original_reset_path, follow=True)
        self.assertContains(reused_response, "الرابط غير صالح")

    def test_recovery_pages_are_not_indexable(self):
        for route_name in (
            "reports:password_reset",
            "reports:password_reset_done",
            "reports:password_reset_complete",
        ):
            response = self.client.get(reverse(route_name))
            self.assertEqual(
                response.headers.get("X-Robots-Tag"),
                "noindex, nofollow, noarchive",
            )
