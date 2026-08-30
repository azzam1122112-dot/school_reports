from types import SimpleNamespace
from unittest.mock import Mock, patch

from django.test import SimpleTestCase, override_settings

from reports.pdf_invoice import generate_invoice_pdf
from reports.pdf_meeting import generate_meeting_pdf
from reports.pdf_render import render_html_pdf
from reports.pdf_report import generate_report_pdf


class ArabicPdfRendererContractTests(SimpleTestCase):
    @patch("reports.pdf_render._weasy_html")
    def test_html_renderer_embeds_complete_static_font(self, html_factory):
        document = Mock()
        document.write_pdf.return_value = b"%PDF-1.7\nArabic"
        html_factory.return_value = document

        payload = render_html_pdf(html="<p>محضر اجتماع</p>", base_url=None)

        self.assertTrue(payload.startswith(b"%PDF-"))
        html_factory.assert_called_once_with(
            html="<p>محضر اجتماع</p>", base_url=None
        )
        document.write_pdf.assert_called_once_with(full_fonts=True, hinting=True)

    @patch("reports.pdf_render._weasy_html")
    def test_html_renderer_rejects_non_pdf_payload(self, html_factory):
        html_factory.return_value.write_pdf.return_value = b"broken"

        with self.assertRaisesMessage(ValueError, "invalid PDF"):
            render_html_pdf(html="<p>اختبار</p>", base_url=None)


class OfficialArabicPdfSelectionTests(SimpleTestCase):
    @patch("reports.pdf_meeting._generate_meeting_pdf_weasy")
    @patch("reports.pdf_meeting._generate_meeting_pdf_fallback")
    @patch("reports.pdf_meeting.render_to_string", return_value="<html></html>")
    @patch("reports.pdf_meeting.build_meeting_print_context", return_value={})
    def test_meeting_uses_reportlab_by_default(
        self, _context, _template, fallback, weasy
    ):
        fallback.return_value = b"%PDF-1.4\nmeeting"

        payload, filename = generate_meeting_pdf(
            request=None, meeting=SimpleNamespace(pk=17, school=None)
        )

        self.assertEqual(filename, "meeting_17.pdf")
        self.assertTrue(payload.startswith(b"%PDF-"))
        fallback.assert_called_once()
        weasy.assert_not_called()

    @patch("reports.pdf_report._generate_report_pdf_weasy")
    @patch("reports.pdf_report._generate_report_pdf_fallback")
    @patch("reports.pdf_report.render_to_string", return_value="<html></html>")
    @patch("reports.pdf_report.build_report_print_context", return_value={})
    def test_report_uses_reportlab_by_default(
        self, _context, _template, fallback, weasy
    ):
        fallback.return_value = b"%PDF-1.4\nreport"

        payload, filename = generate_report_pdf(
            request=None, report=SimpleNamespace(id=23)
        )

        self.assertEqual(filename, "report_23.pdf")
        self.assertTrue(payload.startswith(b"%PDF-"))
        fallback.assert_called_once()
        weasy.assert_not_called()

    @patch("reports.pdf_invoice._generate_invoice_pdf_weasy")
    @patch("reports.pdf_invoice._generate_invoice_pdf_fallback")
    @patch("reports.pdf_invoice.render_to_string", return_value="<html></html>")
    def test_invoice_uses_reportlab_by_default(self, _template, fallback, weasy):
        fallback.return_value = b"%PDF-1.4\ninvoice"

        payload, filename = generate_invoice_pdf(
            context={"invoice_number": "INV-42"}, request=None
        )

        self.assertEqual(filename, "tawtheeq-invoice-INV-42.pdf")
        self.assertTrue(payload.startswith(b"%PDF-"))
        fallback.assert_called_once()
        weasy.assert_not_called()

    @override_settings(PDF_ARABIC_RENDERER="weasyprint")
    @patch("reports.pdf_meeting._generate_meeting_pdf_weasy")
    @patch("reports.pdf_meeting._generate_meeting_pdf_fallback")
    @patch("reports.pdf_meeting.render_to_string", return_value="<html></html>")
    @patch("reports.pdf_meeting.build_meeting_print_context", return_value={})
    def test_weasyprint_can_be_enabled_explicitly(
        self, _context, _template, fallback, weasy
    ):
        weasy.return_value = b"%PDF-1.7\nmeeting"

        payload, _filename = generate_meeting_pdf(
            request=None, meeting=SimpleNamespace(pk=19, school=None)
        )

        self.assertTrue(payload.startswith(b"%PDF-"))
        weasy.assert_called_once()
        fallback.assert_not_called()
