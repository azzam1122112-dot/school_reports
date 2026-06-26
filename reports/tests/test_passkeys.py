from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import Teacher


@override_settings(ALLOWED_HOSTS=["testserver"])
class PasskeyEndpointTests(TestCase):
    def setUp(self):
        self.user = Teacher.objects.create_user(
            phone="555000111",
            name="Passkey User",
            password="pass",
        )

    def test_registration_options_require_login(self):
        response = self.client.post(reverse("reports:passkey_register_options"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

    def test_registration_options_return_webauthn_payload(self):
        self.client.force_login(self.user)

        response = self.client.post(reverse("reports:passkey_register_options"))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("challenge", payload["publicKey"])
        self.assertEqual(payload["publicKey"]["rp"]["id"], "testserver")
        self.assertEqual(payload["publicKey"]["user"]["name"], self.user.phone)

    def test_login_options_are_available_before_login(self):
        response = self.client.post(
            reverse("reports:passkey_login_options"),
            data='{"identifier":"555000111"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("challenge", payload["publicKey"])
        self.assertEqual(payload["publicKey"]["rpId"], "testserver")

    def test_login_verify_without_challenge_is_rejected(self):
        response = self.client.post(
            reverse("reports:passkey_login_verify"),
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "challenge_missing")
