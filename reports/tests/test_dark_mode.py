from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(ALLOWED_HOSTS=["testserver"], SITE_URL="https://tawtheeq.example")
class DarkModeExperienceTests(TestCase):
    @staticmethod
    def _source(relative_path: str) -> str:
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    def test_public_pages_load_the_shared_theme_without_a_light_flash(self):
        for route_name in (
            "reports:landing",
            "reports:login",
            "reports:register_school",
            "reports:user_guide",
        ):
            with self.subTest(route_name=route_name):
                response = self.client.get(reverse(route_name))
                html = response.content.decode("utf-8")

                self.assertEqual(response.status_code, 200)
                self.assertIn('name="color-scheme" content="light dark"', html)
                self.assertIn("localStorage.getItem('theme')", html)
                self.assertIn("prefers-color-scheme: dark", html)
                self.assertIn("css/dark-mode.css", html)
                self.assertIn("js/theme-manager.js", html)
                self.assertLess(html.index("localStorage.getItem('theme')"), html.index("css/dark-mode.css"))

    def test_all_interactive_standalone_templates_share_the_theme_layer(self):
        templates = (
            "reports/templates/reports/landing.html",
            "reports/templates/reports/login.html",
            "reports/templates/reports/register_school.html",
            "reports/templates/reports/registration_success.html",
            "reports/templates/reports/maintenance_mode.html",
            "reports/templates/reports/password_reset_base.html",
            "reports/templates/reports/user_guide.html",
        )
        for template_path in templates:
            with self.subTest(template_path=template_path):
                source = self._source(template_path)
                self.assertIn('name="color-scheme" content="light dark"', source)
                self.assertIn('include "reports/partials/theme_bootstrap.html"', source)
                self.assertIn("css/dark-mode.css", source)
                self.assertIn("js/theme-manager.js", source)

    def test_shared_application_template_uses_the_single_theme_manager(self):
        source = self._source("reports/templates/base.html")

        self.assertIn('include "reports/partials/theme_bootstrap.html"', source)
        self.assertIn("css/dark-mode.css", source)
        self.assertIn("js/theme-manager.js", source)
        self.assertNotIn("// ===== Theme (data-theme) =====", source)

    def test_theme_manager_persists_and_exposes_accessible_state(self):
        javascript = self._source("static/js/theme-manager.js")

        self.assertIn("window.localStorage.setItem(storageKey, theme)", javascript)
        self.assertIn("max-age=31536000; SameSite=Lax", javascript)
        self.assertIn("prefers-color-scheme: dark", javascript)
        self.assertIn("media.addEventListener('change', followSystem)", javascript)
        self.assertIn("button.setAttribute('aria-pressed'", javascript)
        self.assertIn("تم تفعيل الوضع الليلي", javascript)
        self.assertIn("new CustomEvent('themechange'", javascript)

    def test_dark_styles_cover_core_controls_and_preserve_print_outputs(self):
        css = self._source("static/css/dark-mode.css")

        self.assertIn('html[data-theme="dark"] input', css)
        self.assertIn('html[data-theme="dark"] thead th', css)
        self.assertIn("input:-webkit-autofill", css)
        self.assertIn(".theme-toggle--floating", css)
        self.assertIn("@media print", css)

        for print_template in (
            "reports/templates/reports/report_print.html",
            "reports/templates/reports/ticket_print.html",
            "reports/templates/reports/notification_signatures_print.html",
        ):
            with self.subTest(print_template=print_template):
                source = self._source(print_template)
                self.assertNotIn("theme-manager.js", source)
                self.assertNotIn("dark-mode.css", source)
