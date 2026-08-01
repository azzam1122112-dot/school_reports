import hashlib

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import Teacher, WebAuthnCredential
from reports.views.auth import PASSKEY_ENROLL_PROMPT_SESSION_KEY
from reports.webauthn import (
    b64url_encode,
    credential_hash,
    parse_authenticator_data,
)


@override_settings(ALLOWED_HOSTS=["testserver"])
class PasskeyEndpointTests(TestCase):
    def setUp(self):
        self.user = Teacher.objects.create_user(
            phone="555000111",
            name="Passkey User",
            password="pass",
        )
        self.credential_id = b"passkey-user-credential"
        WebAuthnCredential.objects.create(
            teacher=self.user,
            credential_id=self.credential_id,
            credential_id_hash=credential_hash(self.credential_id),
            public_key_cose=b"public-key",
            transports=["internal"],
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
        self.assertEqual(
            payload["publicKey"]["authenticatorSelection"]["userVerification"],
            "required",
        )
        self.assertEqual(
            payload["publicKey"]["authenticatorSelection"]["residentKey"],
            "required",
        )
        self.assertTrue(
            payload["publicKey"]["authenticatorSelection"]["requireResidentKey"],
        )
        self.assertNotIn(
            "authenticatorAttachment",
            payload["publicKey"]["authenticatorSelection"],
        )

    def test_password_login_offers_optional_passkey_enrollment(self):
        user = Teacher.objects.create_user(
            phone="555000444",
            name="New Passkey User",
            password="safe-pass",
        )

        response = self.client.post(
            reverse("reports:login"),
            {"phone": user.phone, "password": "safe-pass"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.client.session[PASSKEY_ENROLL_PROMPT_SESSION_KEY])

        profile_response = self.client.get(reverse("reports:my_profile"))
        self.assertContains(profile_response, 'id="passkeyEnrollmentPrompt"')
        self.assertContains(profile_response, "تفعيل الآن")
        self.assertContains(profile_response, "ليس الآن")
        self.assertContains(profile_response, "لا تُرسل إلى المنصة")

    def test_password_login_does_not_prompt_user_with_active_passkey(self):
        response = self.client.post(
            reverse("reports:login"),
            {"phone": self.user.phone, "password": "pass"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertNotIn(PASSKEY_ENROLL_PROMPT_SESSION_KEY, self.client.session)
        profile_response = self.client.get(reverse("reports:my_profile"))
        self.assertNotContains(profile_response, 'id="passkeyEnrollmentPrompt"')

    def test_password_change_requirement_takes_priority_over_passkey_prompt(self):
        user = Teacher.objects.create_user(
            phone="555000555",
            name="Default Password User",
            password="555000555",
        )

        response = self.client.post(
            reverse("reports:login"),
            {"phone": user.phone, "password": user.phone},
        )

        self.assertRedirects(
            response,
            reverse("reports:my_profile"),
            fetch_redirect_response=False,
        )
        self.assertNotIn(PASSKEY_ENROLL_PROMPT_SESSION_KEY, self.client.session)

    def test_user_can_dismiss_optional_prompt_for_current_session(self):
        user = Teacher.objects.create_user(
            phone="555000666",
            name="Dismiss Passkey User",
            password="safe-pass",
        )
        self.client.force_login(user)
        session = self.client.session
        session[PASSKEY_ENROLL_PROMPT_SESSION_KEY] = True
        session.save()

        response = self.client.post(reverse("reports:passkey_enroll_prompt_dismiss"))

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertNotIn(PASSKEY_ENROLL_PROMPT_SESSION_KEY, self.client.session)

    def test_dismiss_endpoint_requires_login(self):
        response = self.client.post(reverse("reports:passkey_enroll_prompt_dismiss"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("/login/", response["Location"])

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
        self.assertEqual(payload["publicKey"]["userVerification"], "required")
        self.assertEqual(payload["publicKey"]["allowCredentials"][0]["id"], b64url_encode(self.credential_id))

    def test_login_options_require_identifier(self):
        response = self.client.post(
            reverse("reports:passkey_login_options"),
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "identifier_required")

    def test_login_options_reject_user_without_passkey(self):
        Teacher.objects.create_user(
            phone="555000222",
            name="No Passkey User",
            password="pass",
        )

        response = self.client.post(
            reverse("reports:passkey_login_options"),
            data='{"identifier":"555000222"}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["error"], "passkey_not_enabled")

    def test_login_verify_rejects_credential_not_allowed_for_identifier(self):
        other_user = Teacher.objects.create_user(
            phone="555000333",
            name="Other Passkey User",
            password="pass",
        )
        other_credential_id = b"other-user-credential"
        WebAuthnCredential.objects.create(
            teacher=other_user,
            credential_id=other_credential_id,
            credential_id_hash=credential_hash(other_credential_id),
            public_key_cose=b"other-public-key",
            transports=["internal"],
        )

        self.client.post(
            reverse("reports:passkey_login_options"),
            data='{"identifier":"555000111"}',
            content_type="application/json",
        )
        response = self.client.post(
            reverse("reports:passkey_login_verify"),
            data=f'{{"rawId":"{b64url_encode(other_credential_id)}"}}',
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["error"], "credential_not_allowed")

    def test_login_verify_without_challenge_is_rejected(self):
        response = self.client.post(
            reverse("reports:passkey_login_verify"),
            data="{}",
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["error"], "challenge_missing")


class PasskeyRpIdTests(TestCase):
    """معرّف النطاق الثابت يجعل البصمة تعمل عبر النطاقات الفرعية."""

    def _rp_id(self, host):
        from django.test import RequestFactory

        from reports.webauthn import rp_id_from_request

        request = RequestFactory().get("/", HTTP_HOST=host)
        return rp_id_from_request(request)

    @override_settings(ALLOWED_HOSTS=["app.tawtheeq-ksa.com", "www.tawtheeq-ksa.com", "tawtheeq-ksa.com"])
    def test_configured_rp_id_used_for_subdomains(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"WEBAUTHN_RP_ID": "tawtheeq-ksa.com"}):
            self.assertEqual(self._rp_id("app.tawtheeq-ksa.com"), "tawtheeq-ksa.com")
            self.assertEqual(self._rp_id("www.tawtheeq-ksa.com"), "tawtheeq-ksa.com")
            self.assertEqual(self._rp_id("tawtheeq-ksa.com"), "tawtheeq-ksa.com")

    @override_settings(ALLOWED_HOSTS=["example-unrelated-host.com"])
    def test_configured_rp_id_ignored_for_unrelated_host(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {"WEBAUTHN_RP_ID": "tawtheeq-ksa.com"}):
            self.assertEqual(self._rp_id("example-unrelated-host.com"), "example-unrelated-host.com")

    @override_settings(ALLOWED_HOSTS=["testserver"])
    def test_falls_back_to_host_without_config(self):
        import os
        from unittest import mock

        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("WEBAUTHN_RP_ID", None)
            self.assertEqual(self._rp_id("testserver"), "testserver")


class PasskeyUserVerificationTests(TestCase):
    def _authenticator_data(self, rp_id: str, flags: int) -> bytes:
        return hashlib.sha256(rp_id.encode("utf-8")).digest() + bytes([flags]) + (0).to_bytes(4, "big")

    def test_user_verification_flag_is_required_when_requested(self):
        with self.assertRaisesRegex(ValueError, "user_verification_required"):
            parse_authenticator_data(
                self._authenticator_data("testserver", 0x01),
                rp_id="testserver",
                require_user_verification=True,
            )

    def test_verified_user_flag_is_accepted(self):
        parsed = parse_authenticator_data(
            self._authenticator_data("testserver", 0x05),
            rp_id="testserver",
            require_user_verification=True,
        )

        self.assertEqual(parsed.flags, 0x05)
