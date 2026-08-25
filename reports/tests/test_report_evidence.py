from __future__ import annotations

from datetime import date
from io import BytesIO
import re
import tempfile
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import IntegrityError, transaction
from django.template.loader import render_to_string
from django.test import TestCase, override_settings

from PIL import Image, ImageDraw

from reports.forms import ReportEvidenceForm
from reports.models import Report, ReportEvidence, School, Teacher
from reports.pdf_report import _generate_report_pdf_weasy, build_report_print_context


def image_upload(name: str, size: tuple[int, int], *, alpha=False, orientation=None, quality=92):
    mode = "RGBA" if alpha else "RGB"
    background = (245, 249, 247, 180) if alpha else (245, 249, 247)
    image = Image.new(mode, size, background)
    draw = ImageDraw.Draw(image)
    draw.rectangle((8, 8, size[0] - 9, size[1] - 9), outline=(0, 108, 53), width=max(2, min(size) // 80))
    draw.line((0, 0, size[0], size[1]), fill=(185, 151, 91), width=max(2, min(size) // 100))
    output = BytesIO()
    kwargs = {}
    if orientation:
        exif = Image.Exif()
        exif[274] = orientation
        kwargs["exif"] = exif
    fmt = "PNG" if alpha else "JPEG"
    if fmt == "JPEG":
        kwargs["quality"] = quality
    image.save(output, format=fmt, **kwargs)
    content_type = "image/png" if alpha else "image/jpeg"
    return SimpleUploadedFile(name, output.getvalue(), content_type=content_type)


class ReportEvidenceImageTests(TestCase):
    def setUp(self):
        self.media = tempfile.TemporaryDirectory()
        self.addCleanup(self.media.cleanup)
        self.override = override_settings(MEDIA_ROOT=self.media.name)
        self.override.enable()
        self.addCleanup(self.override.disable)
        self.school = School.objects.create(name="مدرسة الشواهد", code="evidence-school")
        self.teacher = Teacher.objects.create_user(phone="0500777101", name="معلم الشواهد", password="Passw0rd!123")
        self.report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="تقرير الشواهد المصورة",
            report_date=date(2026, 8, 13),
            idea="توثيق بصري متكامل للنشاط.",
        )

    def _save_form(self, upload, *, order=1, description="صورة من تنفيذ النشاط"):
        form = ReportEvidenceForm(
            data={
                "order": order,
                "description": description,
                "display_size": ReportEvidence.DisplaySize.AUTO,
                "fit_mode": ReportEvidence.FitMode.CONTAIN,
                "show_in_print": "on",
            },
            files={"image": upload},
        )
        self.assertTrue(form.is_valid(), form.errors)
        evidence = form.save(commit=False)
        evidence.report = self.report
        evidence.save()
        return evidence

    def test_real_portrait_landscape_screenshot_and_alpha_images_are_normalized(self):
        cases = (
            ("portrait.jpg", (900, 1500), False),
            ("landscape.jpg", (1800, 900), False),
            ("screenshot.png", (1440, 2560), True),
        )
        for order, (name, size, alpha) in enumerate(cases, start=1):
            evidence = self._save_form(image_upload(name, size, alpha=alpha), order=order)
            self.assertTrue(evidence.image.name.endswith(".webp"))
            with Image.open(evidence.image.path) as stored:
                self.assertLessEqual(max(stored.size), 2000)
                if alpha:
                    self.assertIn("A", stored.mode)

    def test_exif_rotated_image_is_stored_upright(self):
        evidence = self._save_form(
            image_upload("rotated.jpg", (1200, 700), orientation=6)
        )
        evidence.refresh_from_db()

        self.assertLess(evidence.width_px, evidence.height_px)
        with Image.open(evidence.image.path) as stored:
            self.assertLess(stored.width, stored.height)
            self.assertEqual(stored.getexif().get(274), None)

    def test_four_large_images_use_the_four_item_layout_and_weasy_renders(self):
        for order in range(1, 5):
            self._save_form(
                image_upload(f"large-{order}.jpg", (2600, 1800), quality=96),
                order=order,
                description=f"نموذج من أعمال الطلاب {order}",
            )

        context = build_report_print_context(self.report)
        self.assertEqual(context["EVIDENCE_COUNT"], 4)
        self.assertEqual(context["EVIDENCE_LAYOUT"], 4)
        self.assertFalse(context["EVIDENCE_SEPARATE_PAGE"])
        html = render_to_string("reports/report_print.html", context)
        self.assertIn("images-grid--4", html)
        self.assertIn("نموذج من أعمال الطلاب 1", html)
        self.assertLess(
            html.index("المرفقات والشواهد"),
            html.index('<div class="signature-spacer">'),
        )

        try:
            pdf = _generate_report_pdf_weasy(html=html, base_url=None)
        except (ImportError, OSError) as exc:
            self.skipTest(f"WeasyPrint unavailable: {exc}")
        self.assertTrue(pdf.startswith(b"%PDF-"))
        self.assertGreater(len(pdf), 20_000)
        self.assertEqual(len(re.findall(rb"/Type\s*/Page\b", pdf)), 1)

    def test_legacy_separate_choice_is_folded_into_the_report_page(self):
        self._save_form(image_upload("legacy-separate-page.jpg", (1600, 900)))
        self.report.evidence_page_mode = Report.EvidencePageMode.SEPARATE
        self.report.save(update_fields=["evidence_page_mode"])

        context = build_report_print_context(self.report)

        self.assertFalse(context["EVIDENCE_SEPARATE_PAGE"])

    def test_mobile_report_lists_use_evidence_records_not_only_legacy_slots(self):
        source = (Path(settings.BASE_DIR) / "reports/templates/reports/my_reports.html").read_text(encoding="utf-8")

        self.assertIn("report.evidences.all", source)
        self.assertIn("evidence.description", source)

    def test_report_form_no_longer_accepts_legacy_image_slots(self):
        from reports.forms import ReportForm

        form = ReportForm()
        for field_name in ("image1", "image2", "image3", "image4"):
            self.assertNotIn(field_name, form.fields)
        self.assertIn("client_submission_id", form.fields)
        self.assertTrue(form.fields["evidence_page_mode"].widget.is_hidden)

    def test_submission_key_prevents_duplicate_report_rows(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Report.objects.create(
                school=self.school,
                teacher=self.teacher,
                title="إرسال مكرر",
                report_date=date(2026, 8, 13),
                submission_key=self.report.submission_key,
            )
