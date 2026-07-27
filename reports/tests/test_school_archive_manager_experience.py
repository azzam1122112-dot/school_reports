import re
import tempfile
import zipfile
from datetime import timedelta
from io import BytesIO
from unittest.mock import patch

from django.core.exceptions import ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from maintenance.services import collect_reset_summary
from reports.models import (
    Payment,
    Notification,
    NotificationRecipient,
    Report,
    School,
    SchoolArchiveAddon,
    SchoolMembership,
    SchoolSubscription,
    SchoolYearArchive,
    SchoolYearArchiveDownload,
    SubscriptionPlan,
    Teacher,
    Ticket,
    TicketImage,
)
from reports.services_archive import archive_available_years
from reports.services_export import build_school_export_zip_file


@override_settings(ALLOWED_HOSTS=["testserver"])
class SchoolArchiveManagerExperienceTests(TestCase):
    def setUp(self):
        self.media_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.media_dir.cleanup)
        self.settings_override = override_settings(MEDIA_ROOT=self.media_dir.name)
        self.settings_override.enable()
        self.addCleanup(self.settings_override.disable)

        self.school = School.objects.create(
            name="مدرسة الأرشيف الاحترافي",
            code="archive-manager-ux",
            current_academic_year="1447-1448",
            allowed_academic_years=["1446-1447", "1447-1448"],
        )
        plan = SubscriptionPlan.objects.create(
            name="خطة الأرشيف",
            price=500,
            days_duration=365,
            max_teachers=0,
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.addon = SchoolArchiveAddon.objects.create(
            school=self.school,
            is_enabled=True,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=20),
            storage_limit_gb=2,
        )
        self.manager = Teacher.objects.create_user(
            phone="0500700001",
            name="مدير الأرشيف",
            password="safe-manager-password",
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.teacher = Teacher.objects.create_user(
            phone="0500700002",
            name="معلم الأرشيف",
            password="safe-teacher-password",
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="تقرير ثابت للأرشيف",
            report_date=timezone.localdate(),
            academic_year="1447-1448",
        )
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def _create_snapshot(self):
        with patch(
            "reports.pdf_report.generate_report_pdf",
            return_value=(b"%PDF-archive-report", "report.pdf"),
        ):
            return self.client.post(
                reverse("reports:school_archive_create"),
                {"year": "1447-1448"},
            )

    def test_manager_archive_page_is_clear_searchable_and_warns_before_expiry(self):
        response = self.client.get(reverse("reports:school_archive"))
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(re.findall(r"<h1\b", html, re.IGNORECASE)), 1)
        self.assertContains(response, "نسخة ZIP ثابتة")
        self.assertContains(response, "تبقى 20 يومًا")
        self.assertContains(response, "اسم التقرير أو المعلم أو الجوال")
        self.assertContains(response, "إنشاء نسخة ثابتة الآن")
        self.assertNotContains(response, "1446-1447 هـ")

    def test_creation_persists_versioned_zip_with_excel_manifest_and_audit_download(self):
        response = self._create_snapshot()

        archive = SchoolYearArchive.objects.get()
        self.assertRedirects(
            response,
            f"{reverse('reports:school_archive')}?year=1447-1448&snapshot={archive.pk}",
            fetch_redirect_response=False,
        )
        self.assertEqual(archive.status, SchoolYearArchive.Status.READY)
        self.assertEqual(archive.version, 1)
        self.assertTrue(archive.archive_sha256)
        self.assertGreater(archive.storage_bytes, 0)
        self.school.refresh_from_db()
        self.assertEqual(self.school.storage_used_bytes, archive.storage_bytes)

        archive.archive_file.open("rb")
        package = archive.archive_file.read()
        archive.archive_file.close()
        with zipfile.ZipFile(BytesIO(package)) as zipped:
            names = set(zipped.namelist())
            self.assertIn("فهرس-السنة.xlsx", names)
            self.assertIn("الفهرس-والتحقق.txt", names)
            self.assertTrue(any(name.endswith("/التقرير.pdf") for name in names))
            manifest = zipped.read("الفهرس-والتحقق.txt").decode("utf-8")
            self.assertIn("اكتمل إنشاء الحزمة دون ملفات مفقودة", manifest)

        download = self.client.get(
            reverse("reports:school_archive_download", args=[archive.pk])
        )
        self.assertEqual(download.status_code, 200)
        self.assertEqual(download["Content-Type"], "application/zip")
        download.close()
        self.assertEqual(
            SchoolYearArchiveDownload.objects.filter(
                archive=archive,
                downloaded_by=self.manager,
            ).count(),
            1,
        )

    def test_each_snapshot_is_a_new_immutable_version(self):
        self._create_snapshot()
        self.report.title = "عنوان حي جديد"
        self.report.save(update_fields=["title"])
        self._create_snapshot()

        versions = list(
            SchoolYearArchive.objects.order_by("version").values_list("version", flat=True)
        )
        self.assertEqual(versions, [1, 2])
        first = SchoolYearArchive.objects.get(version=1)
        first.archive_sha256 = "0" * 64
        with self.assertRaises(ValidationError):
            first.save()

    def test_partial_snapshot_is_saved_with_visible_completion_counts(self):
        with patch(
            "reports.pdf_report.generate_report_pdf",
            side_effect=RuntimeError("PDF renderer unavailable"),
        ):
            response = self.client.post(
                reverse("reports:school_archive_create"),
                {"year": "1447-1448"},
            )

        self.assertEqual(response.status_code, 302)
        archive = SchoolYearArchive.objects.get()
        self.assertEqual(archive.status, SchoolYearArchive.Status.PARTIAL)
        self.assertEqual(archive.failed_pdf_count, 1)

        page = self.client.get(
            reverse("reports:school_archive"),
            {"year": "1447-1448"},
        )
        self.assertContains(page, "مكتمل مع ملاحظات")
        self.assertContains(page, "1 PDF متعذر")

    def test_saved_snapshot_remains_visible_downloadable_and_reset_protected_after_expiry(self):
        self._create_snapshot()
        archive = SchoolYearArchive.objects.get()
        self.addon.end_date = timezone.localdate() - timedelta(days=1)
        self.addon.save(update_fields=["end_date"])

        page = self.client.get(reverse("reports:school_archive"))
        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "النسخ محفوظة")
        self.assertContains(page, "طلب التجديد")
        self.assertContains(page, "تنزيل هذه النسخة")

        download = self.client.get(
            reverse("reports:school_archive_download", args=[archive.pk])
        )
        self.assertEqual(download.status_code, 200)
        download.close()
        summary = collect_reset_summary(
            [self.school],
            {
                "reports": True,
                "achievements": True,
                "tickets": False,
                "notifications": False,
                "share_links": True,
            },
        )
        self.assertEqual(summary["archive_protected_schools_count"], 1)
        self.assertEqual(summary["reports_count"], 0)

    def test_configured_but_empty_years_are_not_presented_as_archived(self):
        self.report.delete()

        years = archive_available_years(
            school=self.school,
            teacher=self.manager,
            school_wide=True,
        )

        self.assertEqual(years, [])

    def test_school_with_only_administrative_records_can_create_current_year_snapshot(self):
        self.report.delete()
        Ticket.objects.create(
            school=self.school,
            creator=self.teacher,
            title="طلب إداري وحيد",
        )

        years = archive_available_years(
            school=self.school,
            teacher=self.manager,
            school_wide=True,
        )
        self.assertIn("1447-1448", years)

        with (
            patch(
                "reports.pdf_archive_records.generate_ticket_archive_pdf",
                return_value=b"%PDF-ticket-record",
            ),
            patch(
                "reports.pdf_archive_records.generate_notification_archive_pdf",
                return_value=b"%PDF-notification-record",
            ),
        ):
            response = self.client.post(
                reverse("reports:school_archive_create"),
                {"year": "1447-1448"},
            )

        self.assertEqual(response.status_code, 302)
        archive = SchoolYearArchive.objects.get()
        self.assertEqual(archive.report_count, 0)
        self.assertEqual(archive.ticket_count, 1)

    def test_one_time_year_export_also_contains_excel_index(self):
        with patch(
            "reports.pdf_report.generate_report_pdf",
            return_value=(b"%PDF-archive-report", "report.pdf"),
        ):
            package, metadata = build_school_export_zip_file(
                self.school,
                academic_year="1447-1448",
                teacher=self.manager,
                school_wide=True,
                return_metadata=True,
            )
        try:
            with zipfile.ZipFile(package) as zipped:
                self.assertIn("فهرس-السنة.xlsx", zipped.namelist())
            self.assertFalse(metadata["is_partial"])
            self.assertEqual(metadata["report_count"], 1)
        finally:
            package.close()

    def test_snapshot_contains_pdf_and_original_files_for_tickets_and_circulars(self):
        ticket = Ticket.objects.create(
            school=self.school,
            creator=self.teacher,
            title="طلب تجهيز مختبر",
            body="تجهيز المختبر قبل الزيارة.",
            attachment=SimpleUploadedFile(
                "ticket.pdf",
                b"%PDF-ticket-attachment",
                content_type="application/pdf",
            ),
        )
        TicketImage.objects.create(
            ticket=ticket,
            image=SimpleUploadedFile(
                "ticket.png",
                bytes.fromhex(
                    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
                    "890000000a49444154789c6360000002000154a24f6f0000000049454e44ae426082"
                ),
                content_type="image/png",
            ),
        )
        circular = Notification.objects.create(
            school=self.school,
            created_by=self.manager,
            title="تعميم السلامة",
            message="الالتزام بإجراءات السلامة.",
            requires_signature=True,
            attachment=SimpleUploadedFile(
                "circular.pdf",
                b"%PDF-circular-attachment",
                content_type="application/pdf",
            ),
        )
        NotificationRecipient.objects.create(
            notification=circular,
            teacher=self.teacher,
        )
        notification = Notification.objects.create(
            school=self.school,
            created_by=self.manager,
            title="إشعار اجتماع",
            message="اجتماع قصير بعد الدوام.",
        )
        NotificationRecipient.objects.create(
            notification=notification,
            teacher=self.teacher,
        )

        with (
            patch(
                "reports.pdf_report.generate_report_pdf",
                return_value=(b"%PDF-report", "report.pdf"),
            ),
            patch(
                "reports.pdf_archive_records.generate_ticket_archive_pdf",
                return_value=b"%PDF-ticket-record",
            ),
            patch(
                "reports.pdf_archive_records.generate_notification_archive_pdf",
                return_value=b"%PDF-notification-record",
            ),
        ):
            response = self.client.post(
                reverse("reports:school_archive_create"),
                {"year": "1447-1448"},
            )

        self.assertEqual(response.status_code, 302)
        archive = SchoolYearArchive.objects.get()
        self.assertEqual(archive.ticket_count, 1)
        self.assertEqual(archive.circular_count, 1)
        self.assertEqual(
            archive.notification_count,
            Notification.objects.filter(
                school=self.school,
                requires_signature=False,
            ).count(),
        )

        archive.archive_file.open("rb")
        package = archive.archive_file.read()
        archive.archive_file.close()
        with zipfile.ZipFile(BytesIO(package)) as zipped:
            names = zipped.namelist()
            self.assertTrue(
                any(
                    name.startswith("الطلبات-والتذاكر/")
                    and name.endswith("/سجل-الطلب.pdf")
                    for name in names
                )
            )
            self.assertTrue(
                any(
                    name.startswith("الطلبات-والتذاكر/")
                    and name.endswith("/المرفق-الرئيسي.pdf")
                    for name in names
                )
            )
            self.assertTrue(
                any(
                    name.startswith("الطلبات-والتذاكر/")
                    and "/صورة-1." in name
                    for name in names
                )
            )
            self.assertTrue(
                any(
                    name.startswith("التعاميم/")
                    and name.endswith("/السجل.pdf")
                    for name in names
                )
            )
            self.assertTrue(
                any(
                    name.startswith("التعاميم/")
                    and name.endswith("/المرفق.pdf")
                    for name in names
                )
            )
            self.assertTrue(
                any(
                    name.startswith("الإشعارات/")
                    and name.endswith("/السجل.pdf")
                    for name in names
                )
            )
            manifest = zipped.read("الفهرس-والتحقق.txt").decode("utf-8")
            self.assertIn("الطلبات والتذاكر حتى لحظة إنشاء النسخة: 1", manifest)
            self.assertIn("التعاميم حتى لحظة إنشاء النسخة: 1", manifest)

        page = self.client.get(
            reverse("reports:school_archive"),
            {"year": "1447-1448"},
        )
        self.assertContains(page, "التذاكر حتى الآن")
        self.assertContains(page, "جميع سجلاتها وحالاتها ومرفقاتها")

    def test_inactive_archive_page_has_direct_activation_cta_and_price(self):
        self.addon.is_enabled = False
        self.addon.save(update_fields=["is_enabled"])

        response = self.client.get(reverse("reports:school_archive"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "طلب التفعيل الآن")
        self.assertContains(response, "سنويًا")
        self.assertContains(response, reverse("reports:my_subscription") + "#archiveOrder")

    def test_non_manager_cannot_create_school_snapshot(self):
        self.client.force_login(self.teacher)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

        response = self.client.post(
            reverse("reports:school_archive_create"),
            {"year": "1447-1448"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(SchoolYearArchive.objects.exists())


@override_settings(ALLOWED_HOSTS=["testserver"])
class ArchivePaymentIdempotencyTests(TestCase):
    def test_reapproving_same_archive_payment_does_not_extend_twice(self):
        school = School.objects.create(name="مدرسة دفعة الأرشيف", code="archive-payment-once")
        manager = Teacher.objects.create_user(
            phone="0500700010",
            name="مدير الدفعة",
            password="manager-password",
        )
        SchoolMembership.objects.create(
            school=school,
            teacher=manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        payment = Payment.objects.create(
            school=school,
            purpose=Payment.Purpose.ARCHIVE_ADDON,
            amount=399,
            created_by=manager,
        )
        admin = Teacher.objects.create_superuser(
            phone="0500700011",
            name="مدير المنصة",
            password="admin-password",
        )
        self.client.force_login(admin)

        self.client.post(
            reverse("reports:platform_payment_detail", args=[payment.pk]),
            {"status": Payment.Status.APPROVED, "notes": ""},
        )
        addon = SchoolArchiveAddon.objects.get(school=school)
        first_end_date = addon.end_date
        payment.refresh_from_db()
        self.assertIsNotNone(payment.effects_applied_at)

        self.client.post(
            reverse("reports:platform_payment_detail", args=[payment.pk]),
            {"status": Payment.Status.REJECTED, "notes": ""},
        )
        self.client.post(
            reverse("reports:platform_payment_detail", args=[payment.pk]),
            {"status": Payment.Status.APPROVED, "notes": ""},
        )

        addon.refresh_from_db()
        self.assertEqual(addon.end_date, first_end_date)
