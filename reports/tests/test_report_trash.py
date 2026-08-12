from datetime import date, timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    Report,
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


@override_settings(ALLOWED_HOSTS=["testserver"])
class ReportTrashTests(TestCase):
    def setUp(self):
        plan = SubscriptionPlan.objects.create(
            name="خطة سلة التقارير",
            price=0,
            days_duration=30,
            max_teachers=10,
            is_active=True,
        )
        self.school = School.objects.create(name="مدرسة السلة", code="trash-school")
        SchoolSubscription.objects.create(
            school=self.school,
            plan=plan,
            start_date=timezone.localdate(),
            end_date=timezone.localdate() + timedelta(days=30),
            is_active=True,
        )
        self.manager = _user("مدير السلة", "0509300001")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.teacher = _user("معلم السلة", "0509300002")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.other = _user("معلم آخر", "0509300003")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.other,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            teacher_name=self.teacher.name,
            title="تقرير قابل للاستعادة",
            idea="تفاصيل",
            report_date=date.today(),
        )
        self.link = ShareLink.objects.create(
            token=ShareLink.generate_token(),
            kind=ShareLink.Kind.REPORT,
            created_by=self.teacher,
            school=self.school,
            report=self.report,
            expires_at=timezone.now() + timedelta(days=7),
        )

    def _enter(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def test_delete_moves_report_to_trash_and_preserves_related_link(self):
        self._enter(self.teacher)

        response = self.client.post(
            reverse("reports:delete_my_report", args=[self.report.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Report.objects.filter(pk=self.report.pk).exists())
        trashed = Report.all_objects.get(pk=self.report.pk)
        self.assertIsNotNone(trashed.trashed_at)
        self.assertEqual(trashed.trashed_by, self.teacher)
        self.assertTrue(ShareLink.objects.filter(pk=self.link.pk).exists())
        self.link.refresh_from_db()
        self.assertFalse(self.link.is_active)
        self.assertEqual(
            self.client.get(reverse("reports:share_public", args=[self.link.token])).status_code,
            404,
        )

    def test_owner_can_restore_their_report(self):
        self.report.move_to_trash(by=self.teacher)
        self._enter(self.teacher)

        response = self.client.post(
            reverse("reports:report_restore", args=[self.report.pk])
        )

        self.assertEqual(response.status_code, 302)
        restored = Report.objects.get(pk=self.report.pk)
        self.assertIsNone(restored.trashed_at)
        self.assertIsNone(restored.trashed_by)
        self.link.refresh_from_db()
        self.assertFalse(self.link.is_active)

    def test_manager_delete_action_uses_the_same_trash(self):
        self._enter(self.manager)

        response = self.client.post(
            reverse("reports:admin_delete_report", args=[self.report.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(Report.objects.filter(pk=self.report.pk).exists())
        self.assertEqual(
            Report.all_objects.get(pk=self.report.pk).trashed_by,
            self.manager,
        )

    def test_report_derives_day_name_when_date_arrives_as_iso_text(self):
        report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            teacher_name=self.teacher.name,
            title="تقرير بتاريخ نصي",
            idea="تفاصيل",
            report_date="2026-08-12",
        )

        self.assertEqual(report.day_name, "الأربعاء")

    def test_manager_sees_school_trash_but_other_teacher_does_not(self):
        self.report.move_to_trash(by=self.teacher)
        self._enter(self.manager)
        manager_page = self.client.get(reverse("reports:report_trash"))

        self._enter(self.other)
        other_page = self.client.get(reverse("reports:report_trash"))

        self.assertContains(manager_page, self.report.title)
        self.assertNotContains(other_page, self.report.title)

    def test_other_teacher_cannot_restore_report(self):
        self.report.move_to_trash(by=self.teacher)
        self._enter(self.other)

        self.client.post(reverse("reports:report_restore", args=[self.report.pk]))

        self.assertFalse(Report.objects.filter(pk=self.report.pk).exists())
        self.assertIsNotNone(Report.all_objects.get(pk=self.report.pk).trashed_at)
