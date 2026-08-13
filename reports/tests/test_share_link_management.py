from datetime import date, timedelta
from io import BytesIO
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    Report,
    ReportEvidence,
    School,
    SchoolMembership,
    SchoolSubscription,
    ShareLink,
    SubscriptionPlan,
    Teacher,
)


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(
        name=name,
        phone=phone,
        email=f"{phone}@example.com",
        password="StrongPass123!",
    )


@override_settings(ALLOWED_HOSTS=["testserver"], SITE_URL="https://example.test")
class ShareLinkManagementTests(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="خطة المشاركة",
            price=0,
            days_duration=30,
            max_teachers=10,
            is_active=True,
        )
        self.school = School.objects.create(name="مدرسة المشاركة", code="share-school")
        SchoolSubscription.objects.create(
            school=self.school,
            plan=plan,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=30),
            is_active=True,
        )
        self.manager = _user("مدير المشاركة", "0509200001")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.teacher = _user("معلم المشاركة", "0509200002")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            teacher_name=self.teacher.name,
            title="تقرير رابط قابل للإدارة",
            idea="تفاصيل التقرير",
            report_date=date.today(),
        )

    def _enter(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def _create_link(self, *, active=True, days=7) -> ShareLink:
        return ShareLink.objects.create(
            token=ShareLink.generate_token(),
            kind=ShareLink.Kind.REPORT,
            created_by=self.teacher,
            school=self.school,
            report=self.report,
            is_active=active,
            expires_at=timezone.now() + timedelta(days=days),
        )

    def test_owner_can_choose_link_expiry(self):
        self._enter(self.teacher)
        before = timezone.now() + timedelta(days=29, hours=23)

        response = self.client.post(
            reverse("reports:report_share_manage", args=[self.report.pk]),
            {"action": "enable", "expiry_days": "30"},
        )

        self.assertEqual(response.status_code, 302)
        link = ShareLink.objects.get(report=self.report, is_active=True)
        self.assertGreater(link.expires_at, before)
        self.assertLess(link.expires_at, timezone.now() + timedelta(days=30, minutes=1))

    def test_public_open_increments_usage_counter(self):
        link = self._create_link()

        first = self.client.get(reverse("reports:share_public", args=[link.token]))
        second = self.client.get(reverse("reports:share_public", args=[link.token]))
        link.refresh_from_db()

        self.assertEqual(first.status_code, 200)
        self.assertEqual(second.status_code, 200)
        self.assertEqual(link.access_count, 2)
        self.assertIsNotNone(link.last_accessed_at)

    def test_public_report_uses_ordered_evidence_images_without_exposing_storage_url(self):
        from PIL import Image

        output = BytesIO()
        Image.new("RGB", (320, 220), (245, 249, 247)).save(output, format="PNG")
        upload = SimpleUploadedFile("evidence.png", output.getvalue(), content_type="image/png")

        with tempfile.TemporaryDirectory() as media_root, self.settings(MEDIA_ROOT=media_root):
            ReportEvidence.objects.create(
                report=self.report,
                image=upload,
                order=1,
                description="صورة من تنفيذ النشاط",
            )
            link = self._create_link()
            image_url = reverse("reports:share_report_image", args=[link.token, 1])

            page = self.client.get(reverse("reports:share_public", args=[link.token]))
            image_response = self.client.get(image_url)

            self.assertEqual(page.status_code, 200)
            self.assertContains(page, image_url)
            self.assertContains(page, "صورة من تنفيذ النشاط")
            self.assertEqual(image_response.status_code, 200)
            self.assertEqual(b"".join(image_response.streaming_content)[:8], b"\x89PNG\r\n\x1a\n")

    def test_teacher_dashboard_only_contains_links_for_their_work(self):
        own_link = self._create_link()
        other = _user("معلم آخر", "0509200003")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=other,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        other_report = Report.objects.create(
            school=self.school,
            teacher=other,
            teacher_name=other.name,
            title="تقرير لا يخص المستخدم",
            idea="تفاصيل",
            report_date=date.today(),
        )
        ShareLink.objects.create(
            token=ShareLink.generate_token(),
            kind=ShareLink.Kind.REPORT,
            created_by=other,
            school=self.school,
            report=other_report,
            expires_at=timezone.now() + timedelta(days=7),
        )
        self._enter(self.teacher)

        response = self.client.get(reverse("reports:share_links_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, own_link.report.title)
        self.assertNotContains(response, other_report.title)

    def test_manager_can_disable_all_school_links(self):
        first = self._create_link()
        second_report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            teacher_name=self.teacher.name,
            title="تقرير ثان",
            idea="تفاصيل",
            report_date=date.today(),
        )
        second = ShareLink.objects.create(
            token=ShareLink.generate_token(),
            kind=ShareLink.Kind.REPORT,
            created_by=self.teacher,
            school=self.school,
            report=second_report,
            expires_at=timezone.now() + timedelta(days=7),
        )
        self._enter(self.manager)

        response = self.client.post(
            reverse("reports:share_links_dashboard"),
            {"action": "disable_all"},
        )

        self.assertEqual(response.status_code, 302)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertFalse(second.is_active)

    def test_manager_cannot_disable_another_schools_link(self):
        other_school = School.objects.create(name="مدرسة بعيدة", code="share-other")
        other_report = Report.objects.create(
            school=other_school,
            teacher=self.teacher,
            teacher_name=self.teacher.name,
            title="تقرير مدرسة بعيدة",
            idea="تفاصيل",
            report_date=date.today(),
        )
        foreign_link = ShareLink.objects.create(
            token=ShareLink.generate_token(),
            kind=ShareLink.Kind.REPORT,
            created_by=self.teacher,
            school=other_school,
            report=other_report,
            expires_at=timezone.now() + timedelta(days=7),
        )
        self._create_link()
        self._enter(self.manager)

        self.client.post(
            reverse("reports:share_links_dashboard"),
            {"action": "disable_all"},
        )
        foreign_link.refresh_from_db()

        self.assertTrue(foreign_link.is_active)
