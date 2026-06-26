import datetime

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase

from reports.models import Report, School, Teacher


def _png(nbytes=2000):
    # رأس PNG صالح + حشو للوصول لحجم محدد تقريبًا
    head = bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c6360000002000154a24f6f0000000049454e44ae426082"
    )
    pad = b"\x00" * max(0, nbytes - len(head))
    return head + pad


class StorageTrackingSignalsTests(TestCase):
    def setUp(self):
        self.school = School.objects.create(name="مدرسة", code="track-school")
        self.teacher = Teacher.objects.create_user(
            phone="500111000", name="معلم", password="pass"
        )

    def _school_used(self):
        return School.objects.values_list("storage_used_bytes", flat=True).get(
            pk=self.school.pk
        )

    def test_create_increments_school_total(self):
        img = SimpleUploadedFile("a.png", _png(3000), content_type="image/png")
        r = Report.objects.create(
            teacher=self.teacher,
            school=self.school,
            title="t",
            report_date=datetime.date(2026, 1, 1),
            image1=img,
        )
        used = self._school_used()
        self.assertGreater(used, 0)
        self.assertEqual(used, Report.objects.values_list("storage_bytes", flat=True).get(pk=r.pk))

    def test_delete_decrements_school_total(self):
        img = SimpleUploadedFile("b.png", _png(3000), content_type="image/png")
        r = Report.objects.create(
            teacher=self.teacher,
            school=self.school,
            title="t",
            report_date=datetime.date(2026, 1, 1),
            image1=img,
        )
        before = self._school_used()
        self.assertGreater(before, 0)
        r.delete()
        self.assertEqual(self._school_used(), 0)

    def test_text_only_edit_does_not_change_total(self):
        img = SimpleUploadedFile("c.png", _png(3000), content_type="image/png")
        r = Report.objects.create(
            teacher=self.teacher,
            school=self.school,
            title="t",
            report_date=datetime.date(2026, 1, 1),
            image1=img,
        )
        used_before = self._school_used()
        r.title = "عنوان جديد"
        r.save()
        self.assertEqual(self._school_used(), used_before)

    def test_no_files_no_change(self):
        Report.objects.create(
            teacher=self.teacher,
            school=self.school,
            title="t",
            report_date=datetime.date(2026, 1, 1),
        )
        self.assertEqual(self._school_used(), 0)
