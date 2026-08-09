import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase


class NotificationAlertExperienceTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.project_root = Path(settings.BASE_DIR)

    def _frontend_sources(self):
        roots = (
            self.project_root / "reports" / "templates",
            self.project_root / "static" / "js",
        )
        for root in roots:
            for pattern in ("*.html", "*.js"):
                for path in root.rglob(pattern):
                    yield path, path.read_text(encoding="utf-8")

    def test_frontend_has_no_native_alert_confirm_or_prompt_calls(self):
        native_dialog = re.compile(
            r"(?<![\w.])(?:window\.)?(?:alert|confirm|prompt)\s*\(",
            re.IGNORECASE,
        )
        offenders = [
            str(path.relative_to(self.project_root))
            for path, source in self._frontend_sources()
            if native_dialog.search(source)
        ]
        self.assertEqual(offenders, [])

    def test_templates_have_no_inline_event_attributes(self):
        inline_event = re.compile(
            r"\son(?:click|submit|change|input)\s*=",
            re.IGNORECASE,
        )
        offenders = [
            str(path.relative_to(self.project_root))
            for path, source in self._frontend_sources()
            if path.suffix == ".html" and inline_event.search(source)
        ]
        self.assertEqual(offenders, [])

    def test_unified_confirm_handles_submitter_and_keyboard_focus(self):
        source = (self.project_root / "reports" / "templates" / "base.html").read_text(
            encoding="utf-8"
        )
        self.assertIn("submitter.matches('[data-confirm]')", source)
        self.assertIn("source.getAttribute('data-reason-prompt')", source)
        self.assertIn("source.getAttribute('data-refund-prompt')", source)
        self.assertIn("document.activeElement === btnOk", source)
        self.assertIn("input.disabled = currentMode !== 'prompt'", source)
        self.assertNotIn("if(e.key === 'Enter') close(true)", source)

    def test_install_prompt_and_home_notice_are_non_modal(self):
        pwa = (
            self.project_root
            / "reports"
            / "templates"
            / "reports"
            / "partials"
            / "pwa_install.html"
        ).read_text(encoding="utf-8")
        home = (
            self.project_root / "reports" / "templates" / "reports" / "home.html"
        ).read_text(encoding="utf-8")
        self.assertIn('role="region"', pwa)
        self.assertNotIn('aria-modal="true"', pwa)
        self.assertIn('id="homeNotification" role="region"', home)
        self.assertNotIn("data-mark-url", home)

        pwa_script = (
            self.project_root / "static" / "js" / "pwa-install.js"
        ).read_text(encoding="utf-8")
        self.assertIn("var DISMISS_DAYS = 90;", pwa_script)
