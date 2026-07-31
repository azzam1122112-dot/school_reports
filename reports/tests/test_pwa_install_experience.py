from __future__ import annotations

import json
from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SITE_URL="https://tawtheeq.example",
)
class PwaInstallExperienceTests(TestCase):
    @staticmethod
    def _source(relative_path: str) -> str:
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    def test_public_mobile_entry_pages_render_the_shared_installer(self):
        for route_name in (
            "reports:landing",
            "reports:login",
            "reports:user_guide",
            "reports:register_school",
        ):
            response = self.client.get(reverse(route_name))

            self.assertEqual(response.status_code, 200)
            self.assertContains(response, 'id="pwaInstallPrompt"')
            self.assertContains(response, 'id="pwaInstallAction"')
            self.assertContains(response, "css/pwa-install.css")
            self.assertContains(response, "js/pwa-install.js")
            self.assertContains(response, 'rel="manifest"')
            self.assertContains(response, 'rel="apple-touch-icon"')

    def test_all_interactive_standalone_templates_include_the_installer(self):
        templates = (
            "reports/templates/base.html",
            "reports/templates/reports/landing.html",
            "reports/templates/reports/login.html",
            "reports/templates/reports/register_school.html",
            "reports/templates/reports/registration_success.html",
            "reports/templates/reports/maintenance_mode.html",
            "reports/templates/reports/user_guide.html",
        )

        for template_path in templates:
            source = self._source(template_path)
            self.assertIn("reports/partials/pwa_install_head.html", source)
            self.assertIn("reports/partials/pwa_install.html", source)

    def test_installer_covers_native_android_and_manual_ios_paths(self):
        script = self._source("static/js/pwa-install.js")

        self.assertIn('window.addEventListener("beforeinstallprompt"', script)
        self.assertIn('window.addEventListener("appinstalled"', script)
        self.assertIn("isIPadOS", script)
        self.assertIn("إضافة إلى الشاشة الرئيسية", script)
        self.assertIn("تثبيت التطبيق", script)
        self.assertIn("window.sessionStorage", script)
        self.assertNotIn("window.localStorage", script)
        self.assertIn('navigator.serviceWorker.register("/sw.js")', script)

    def test_manifest_has_mobile_install_metadata(self):
        manifest = json.loads(self._source("static/manifest.json"))

        self.assertEqual(manifest["id"], "/")
        self.assertEqual(manifest["start_url"], "/")
        self.assertEqual(manifest["scope"], "/")
        self.assertEqual(manifest["display"], "standalone")
        self.assertEqual(manifest["lang"], "ar")
        self.assertEqual(manifest["dir"], "rtl")
        self.assertFalse(manifest["prefer_related_applications"])
        self.assertEqual(
            {icon["sizes"] for icon in manifest["icons"]},
            {"192x192", "512x512"},
        )
