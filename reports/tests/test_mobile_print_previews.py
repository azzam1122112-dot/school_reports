from pathlib import Path

from django.conf import settings
from django.template.loader import get_template
from django.test import SimpleTestCase


class MobilePrintPreviewRegressionTests(SimpleTestCase):
    templates = (
        "reports/report_print.html",
        "reports/ticket_print.html",
        "reports/pdf/leadership_portfolio.html",
    )

    @staticmethod
    def _source(relative_path: str) -> str:
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    def test_preview_templates_compile(self):
        for template_name in self.templates:
            with self.subTest(template=template_name):
                self.assertIsNotNone(get_template(template_name))

    def test_every_browser_preview_declares_the_device_viewport(self):
        for template_name in self.templates:
            source = self._source(f"reports/templates/{template_name}")
            with self.subTest(template=template_name):
                self.assertIn('name="viewport"', source)
                self.assertIn("width=device-width", source)
                self.assertIn("viewport-fit=cover", source)

    def test_report_preview_wraps_toolbar_copy_on_small_screens(self):
        source = self._source("reports/templates/reports/report_print.html")
        responsive_styles = self._source(
            "reports/templates/reports/partials/report_print_official_styles.html"
        )

        self.assertIn("white-space: normal;", source)
        self.assertIn("overflow-wrap: anywhere;", source)
        self.assertIn("@media screen and (max-width: 820px)", responsive_styles)
        self.assertIn("@media screen and (max-width: 520px)", responsive_styles)
        self.assertIn("@media print", responsive_styles)

    def test_ticket_preview_stacks_document_content_without_affecting_print(self):
        source = self._source("reports/templates/reports/ticket_print.html")

        self.assertIn("@media screen and (max-width: 1024px)", source)
        self.assertIn("@media screen and (max-width: 720px)", source)
        self.assertIn("@media screen and (max-width: 520px)", source)
        self.assertIn(".meta-table tbody", source)
        self.assertIn("min-height: 44px;", source)
        self.assertIn("@media print", source)

    def test_leadership_preview_has_mobile_cards_and_keeps_a4_print_rules(self):
        source = self._source(
            "reports/templates/reports/pdf/leadership_portfolio.html"
        )

        self.assertIn("@media screen and (max-width:1024px)", source)
        self.assertIn("@media screen and (max-width:720px)", source)
        self.assertIn("@media screen and (max-width:420px)", source)
        self.assertIn(".overview,.grid,.report-images{grid-template-columns:1fr}", source)
        self.assertIn(".footer{position:static", source)
        self.assertIn("@media print", source)
        self.assertIn("@page{size:A4", source)
