from __future__ import annotations

import re
from unittest.mock import patch

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from config.settings import _media_querystring_auth_enabled
from core.views import healthz


class HealthzTests(TestCase):
    @override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    )
    def test_healthz_skips_channel_probe_by_default(self):
        with patch("core.views.os.getenv", return_value=""):
            with patch("channels.layers.get_channel_layer") as get_layer:
                response = healthz(object())

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"channels": "skipped"', response.content)
        get_layer.assert_not_called()


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SECURITY_CONTACT_EMAIL="security@example.test",
)
class PublicMetadataTests(SimpleTestCase):
    def test_sitemap_uses_real_named_routes(self):
        response = self.client.get("/sitemap.xml")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        for route_name in (
            "reports:landing",
            "reports:login",
            "reports:user_guide",
            "reports:privacy_policy",
            "reports:faq",
        ):
            self.assertIn(f"http://testserver{reverse(route_name)}", body)
        self.assertNotIn("/user-guide/", body)
        self.assertNotIn("/privacy-policy/", body)

    def test_security_txt_uses_configured_contact_and_privacy_route(self):
        response = self.client.get("/.well-known/security.txt")

        self.assertEqual(response.status_code, 200)
        body = response.content.decode("utf-8")
        self.assertIn("Contact: mailto:security@example.test", body)
        self.assertIn(f"Policy: http://testserver{reverse('reports:privacy_policy')}", body)
        self.assertNotIn("support@example.com", body)


class ContentSecurityPolicyTemplateTests(SimpleTestCase):
    INLINE_SCRIPT_TAG_RE = re.compile(
        r"<script\b(?![^>]*\bsrc\s*=)[^>]*>",
        flags=re.IGNORECASE | re.DOTALL,
    )

    def test_inline_template_scripts_have_csp_nonce(self):
        templates_dir = settings.BASE_DIR / "reports" / "templates"
        missing_nonce = []

        for template_path in templates_dir.rglob("*.html"):
            text = template_path.read_text(encoding="utf-8")
            for match in self.INLINE_SCRIPT_TAG_RE.finditer(text):
                if "nonce=" not in match.group(0):
                    line = text.count("\n", 0, match.start()) + 1
                    missing_nonce.append(
                        f"{template_path.relative_to(settings.BASE_DIR)}:{line}"
                    )

        self.assertEqual(missing_nonce, [], msg=f"Inline scripts missing CSP nonce: {missing_nonce}")


class PrivateMediaSettingsTests(SimpleTestCase):
    def test_private_mode_forces_signed_urls(self):
        self.assertTrue(
            _media_querystring_auth_enabled(
                public_access_enabled=False,
                requested_querystring_auth=False,
            )
        )

    def test_public_mode_honors_explicit_querystring_choice(self):
        self.assertFalse(
            _media_querystring_auth_enabled(
                public_access_enabled=True,
                requested_querystring_auth=False,
            )
        )
        self.assertTrue(
            _media_querystring_auth_enabled(
                public_access_enabled=True,
                requested_querystring_auth=True,
            )
        )
