from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from reports.validators import validate_attachment_file


class UploadFeedbackTests(SimpleTestCase):
    def test_unsupported_extension_lists_allowed_formats(self):
        upload = SimpleUploadedFile("unsafe.svg", b"<svg></svg>", content_type="image/svg+xml")

        with self.assertRaises(ValidationError) as caught:
            validate_attachment_file(upload)

        message = str(caught.exception)
        self.assertIn("الصيغ المسموحة", message)
        self.assertIn("PDF", message)
        self.assertIn("DOCX", message)

    def test_oversized_file_reports_actual_and_maximum_size(self):
        upload = SimpleUploadedFile(
            "large.pdf",
            b"%PDF-" + b"0" * (5 * 1024 * 1024),
            content_type="application/pdf",
        )

        with self.assertRaises(ValidationError) as caught:
            validate_attachment_file(upload)

        message = str(caught.exception)
        self.assertIn("الحد الأقصى 5 ميجابايت", message)
        self.assertIn("اختر ملفًا أصغر", message)
