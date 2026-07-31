from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import Teacher


@override_settings(ALLOWED_HOSTS=["testserver"])
class ForcedPasswordChangeTests(TestCase):
    def setUp(self):
        self.user = Teacher.objects.create_user(
            phone="0557000001",
            name="مستخدم الدخول الأول",
            password="0557000001",
        )
        self.client.force_login(self.user)

    def _password_change_payload(self, *, email: str):
        return {
            "update_password": "1",
            "pwd-email": email,
            "pwd-old_password": self.user.phone,
            "pwd-new_password1": "New-safe-password",
            "pwd-new_password2": "New-safe-password",
        }

    def test_first_login_requires_email_with_password_change(self):
        response = self.client.get(reverse("reports:my_profile"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["force_password_change"])
        self.assertContains(response, 'name="pwd-email"')
        self.assertContains(response, 'autocomplete="email"')
        self.assertContains(response, "لاستعادة كلمة المرور لاحقًا")
        self.assertTrue(response.context["pwd_form"].fields["email"].required)

    def test_email_and_new_password_are_saved_together(self):
        response = self.client.post(
            reverse("reports:my_profile"),
            self._password_change_payload(email="USER@example.com"),
        )

        self.assertRedirects(
            response,
            reverse("reports:my_profile"),
            fetch_redirect_response=False,
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "user@example.com")
        self.assertTrue(self.user.check_password("New-safe-password"))

        profile_response = self.client.get(reverse("reports:my_profile"))
        self.assertFalse(profile_response.context["force_password_change"])
        self.assertContains(profile_response, "user@example.com")

    def test_password_change_rejects_email_used_by_another_account(self):
        Teacher.objects.create_user(
            phone="0557000002",
            name="مستخدم آخر",
            password="safe-password",
            email="existing@example.com",
        )

        response = self.client.post(
            reverse("reports:my_profile"),
            self._password_change_payload(email="EXISTING@example.com"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertFormError(
            response.context["pwd_form"],
            "email",
            "هذا البريد الإلكتروني مستخدم في حساب آخر.",
        )
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, "")
        self.assertTrue(self.user.check_password(self.user.phone))
