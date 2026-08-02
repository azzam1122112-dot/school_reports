from __future__ import annotations

import re
import tempfile
from datetime import date
from pathlib import Path

from django.conf import settings
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings

from reports.models import Report, School, SchoolMembership, Teacher
from reports.pdf_report import _generate_report_pdf_fallback, build_report_print_context


ONE_PIXEL_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000a49444154789c6360000002000154a24f6f0000000049454e44ae426082"
)


class OfficialReportPrintDesignTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        self.media_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.media_override.enable()
        self.addCleanup(self.media_override.disable)

        self.school = School.objects.create(
            name="مدرسة الوثيقة الرسمية",
            code="official-report-print",
            gender=School.Gender.GIRLS,
            stage=School.Stage.PRIMARY,
            current_academic_year="1447-1448",
        )
        self.manager = Teacher.objects.create_user(
            phone="0500999101",
            name="مديرة المدرسة",
            password="safe-manager-password",
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.teacher = Teacher.objects.create_user(
            phone="0500999102",
            name="منفذة التقرير",
            password="safe-teacher-password",
        )
        self.report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="برنامج جودة الممارسات التعليمية",
            report_date=date(2026, 5, 14),
            academic_year="1447-1448",
            beneficiaries_count=35,
            idea="وصف تنفيذي واضح ومختصر للتقرير المدرسي.",
        )

    @staticmethod
    def _source(relative_path: str) -> str:
        return (Path(settings.BASE_DIR) / relative_path).read_text(encoding="utf-8")

    @staticmethod
    def _pdf_page_count(pdf_bytes: bytes) -> int:
        return len(re.findall(rb"/Type\s*/Page\b", pdf_bytes))

    def test_html_print_template_is_self_contained_and_official(self):
        template = self._source("reports/templates/reports/report_print.html")
        styles = self._source(
            "reports/templates/reports/partials/report_print_official_styles.html"
        )

        self.assertIn("وثيقة تقرير مدرسي", template)
        self.assertIn("الوصف التنفيذي", template)
        self.assertIn("الاعتمادات والتوقيعات", template)
        self.assertIn("REP-{{ r.id }}", template)
        self.assertNotIn("css/app.css", template)
        self.assertNotIn("css/royal-theme.css", template)
        self.assertNotIn("MIN_FIT_SCALE", template)
        self.assertIn('content: "منصة توثيق التقارير المدرسية', styles)
        self.assertIn("counter(page)", styles)
        self.assertIn("counter(pages)", styles)
        self.assertIn("break-inside: avoid", styles)

    def test_pdf_context_uses_gendered_labels_and_counts_evidence(self):
        for index in range(1, 5):
            setattr(
                self.report,
                f"image{index}",
                SimpleUploadedFile(
                    f"evidence-{index}.png",
                    ONE_PIXEL_PNG,
                    content_type="image/png",
                ),
            )
        self.report.save(update_fields=[f"image{index}" for index in range(1, 5)])

        context = build_report_print_context(self.report)

        self.assertEqual(context["EVIDENCE_COUNT"], 4)
        self.assertEqual(context["executor_label"], "المنفّذة")
        self.assertEqual(context["SCHOOL_MANAGER_LABEL"], "مديرة المدرسة")
        self.assertEqual(context["SCHOOL_HEAD_OF_DEPARTMENT_LABEL"], "رئيسة القسم")
        self.assertTrue(context["PDF_IMAGE1_URL"].startswith("data:image/png;base64,"))

    def test_fallback_pdf_is_one_page_for_a_short_report_without_images(self):
        context = build_report_print_context(self.report)

        pdf_bytes = _generate_report_pdf_fallback(self.report, context=context)

        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertEqual(self._pdf_page_count(pdf_bytes), 1)
        self.assertGreater(len(pdf_bytes), 10_000)

    def test_fallback_pdf_keeps_four_images_and_signatures_within_two_pages(self):
        for index in range(1, 5):
            setattr(
                self.report,
                f"image{index}",
                SimpleUploadedFile(
                    f"official-evidence-{index}.png",
                    ONE_PIXEL_PNG,
                    content_type="image/png",
                ),
            )
        self.report.save(update_fields=[f"image{index}" for index in range(1, 5)])
        context = build_report_print_context(self.report)

        pdf_bytes = _generate_report_pdf_fallback(self.report, context=context)

        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertLessEqual(self._pdf_page_count(pdf_bytes), 2)
        self.assertGreater(len(pdf_bytes), 10_000)
