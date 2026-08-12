from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.conf import settings
from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SITE_URL="https://tawtheeq.example",
    BUSINESS_SUPPORT_EMAIL="support@example.com",
)
class UserGuideTests(TestCase):
    def test_guide_is_task_focused_responsive_and_nontechnical(self):
        response = self.client.get(reverse("reports:user_guide"))
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "أنجز عملك في منصة توثيق")
        self.assertContains(response, "ما دورك في المنصة؟")
        self.assertContains(response, 'id="guideSearch"')
        self.assertContains(response, 'data-guide-filter="teacher"')
        self.assertContains(response, 'data-guide-filter="manager"')
        self.assertContains(response, 'data-guide-role="teacher"')
        self.assertContains(response, 'id="guideMenuToggle"')
        self.assertContains(response, 'aria-controls="guideSidebar"')
        self.assertContains(response, 'id="guideSidebar"')
        self.assertContains(response, 'id="guideDrawerClose"')
        self.assertContains(response, 'id="guideDrawerOverlay"')
        self.assertContains(response, "img/landing/report-system.png")
        self.assertContains(response, "img/landing/dashboard-system.png")
        self.assertContains(response, "css/user-guide.css")
        self.assertContains(response, "js/user-guide.js")
        self.assertContains(response, "vendor/fontawesome/css/all.min.css")
        self.assertNotIn("cdnjs.cloudflare.com", html)
        self.assertNotIn("SchoolMembership", html)
        self.assertNotIn("active_school_required", html)
        self.assertNotIn("CSRF", html)
        self.assertNotIn("/reports/add/", html)
        self.assertNotIn('class="mobile-index"', html)

    def test_markdown_download_uses_the_simplified_guide(self):
        response = self.client.get(reverse("reports:user_guide_download"))
        content = b"".join(response.streaming_content).decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("دليل استخدام منصة توثيق", content)
        self.assertIn("دليل المعلم", content)
        self.assertIn("دليل مدير المدرسة", content)
        self.assertNotIn("SchoolMembership", content)
        self.assertNotIn("active_school_required", content)
        self.assertNotIn("/reports/", content)

    def test_pdf_uses_the_complete_friendly_content(self):
        rendered = {}

        class FakeHTML:
            def __init__(self, *, string, base_url):
                rendered["html"] = string
                rendered["base_url"] = base_url

            def write_pdf(self):
                return b"%PDF-friendly-guide"

        fake_weasyprint = SimpleNamespace(HTML=FakeHTML)
        with patch.dict(sys.modules, {"weasyprint": fake_weasyprint}):
            response = self.client.get(reverse("reports:user_guide_download_pdf"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn("دليل استخدام منصة توثيق", rendered["html"])
        self.assertIn("أنشئ تقريرًا واضحًا في دقائق", rendered["html"])
        self.assertIn("تابع حالة اشتراك المدرسة", rendered["html"])
        self.assertIn("إذا واجهتك مشكلة", rendered["html"])
        self.assertNotIn("SchoolMembership", rendered["html"])
        self.assertNotIn("static/css/app.css", rendered["html"])


class MobileNavigationRegressionTests(TestCase):
    @staticmethod
    def _source(relative_path: str) -> str:
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    def test_shared_drawer_stays_right_aligned_through_tablet_widths(self):
        template = self._source("reports/templates/base.html")
        app_css = self._source("static/css/app.css")
        mobile_css = self._source("static/css/mobile-professional.css")

        self.assertIn('id="mobileDrawer"', template)
        self.assertIn('role="dialog"', template)
        self.assertIn('aria-modal="true"', template)
        self.assertIn('aria-labelledby="drawerTitle" inert', template)
        self.assertIn("position:sticky;top:0;z-index:3;", app_css)
        self.assertIn(
            "@media (min-width: 769px) and (max-width: 1024px)",
            mobile_css,
        )
        self.assertIn("right: 0 !important;", mobile_css)
        self.assertIn("transform: translateX(105%) !important;", mobile_css)
        self.assertIn("transform: translateX(0) !important;", mobile_css)

    def test_wide_header_never_wraps_and_collapses_before_space_runs_out(self):
        # القواعد نُقلت من ``<style>`` داخل ``base.html`` إلى ``app-shell.css``
        # مع بقية هيكل التطبيق. والفحص يتبعها — فالمقصود سلوك الترويسة عند
        # ضيق الشاشة لا الملف الذي يصفه.
        shell = self._source("static/css/app-shell.css")

        self.assertIn(".site-header .container.hdr { max-width: 1760px; }", shell)
        self.assertIn("flex-wrap: nowrap;", shell)
        self.assertIn("@media (max-width: 1599px)", shell)
        self.assertIn(".hdr-nav { display: none; }", shell)
        self.assertIn("flex-direction: column;", shell)

    def test_account_avatar_uses_the_shared_navigation_drawer(self):
        template = self._source("reports/templates/base.html")

        self.assertIn('id="userAvatar" type="button" aria-haspopup="dialog" aria-controls="mobileDrawer"', template)
        self.assertIn("Legacy account popover replaced by the unified drawer", template)
        self.assertIn("e.target.closest('#hamburger, #userAvatar')", template)
        self.assertIn("toggle(trigger, e);", template)
        self.assertIn("if(avatar) avatar.setAttribute('aria-expanded','true');", template)
        self.assertIn("if(avatar) avatar.setAttribute('aria-expanded','false');", template)

    def test_shared_drawer_ignores_click_after_pointerup(self):
        template = self._source("reports/templates/base.html")

        self.assertIn("if(event.type === 'click' && nowMs < suppressClickUntil) return;", template)
        self.assertIn("if(event.type === 'pointerup') suppressClickUntil = nowMs + 450;", template)

    def test_guide_uses_an_accessible_right_mobile_drawer(self):
        guide_css = self._source("static/css/user-guide.css")
        guide_js = self._source("static/js/user-guide.js")

        self.assertIn(".guide-sidebar.is-open", guide_css)
        self.assertIn("right: 0;", guide_css)
        self.assertIn("left: auto;", guide_css)
        self.assertIn("width: min(370px, 92vw);", guide_css)
        self.assertIn('window.matchMedia("(max-width: 820px)")', guide_js)
        self.assertIn('guideSidebar.setAttribute("inert", "")', guide_js)
        self.assertIn('event.key === "Escape"', guide_js)
