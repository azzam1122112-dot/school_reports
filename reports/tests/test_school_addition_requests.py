from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    School,
    SchoolAdditionRequest,
    SchoolArchiveAddon,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


@override_settings(ALLOWED_HOSTS=["testserver"], RATELIMIT_ENABLE=False)
class SchoolAdditionRequestTests(TestCase):
    def setUp(self):
        self.trial = SubscriptionPlan.objects.create(
            name="التجربة المجانية", price=0, days_duration=30, max_teachers=5
        )
        self.manager = Teacher.objects.create_user(
            phone="0501112233", name="مدير المجموعة", password="pass"
        )
        self.source_school = self._school("المدرسة الأولى", "first-school")
        SchoolMembership.objects.create(
            school=self.source_school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        SchoolSubscription.objects.create(school=self.source_school, plan=self.trial)
        self.admin = Teacher.objects.create_superuser(
            phone="0509998877", name="مدير المنصة", password="pass"
        )

    @staticmethod
    def _school(name, code):
        return School.objects.create(
            name=name,
            code=code,
            stage=School.Stage.PRIMARY,
            gender=School.Gender.BOYS,
            city="الرياض",
        )

    def _login_manager(self):
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.source_school.id
        session.save()

    def test_manager_adds_school_and_it_is_approved_and_linked_automatically(self):
        self._login_manager()
        response = self.client.post(
            reverse("reports:school_addition_requests"),
            {
                "school_name": "مدرسة المستقبل",
                "stage": School.Stage.MIDDLE,
                "gender": School.Gender.GIRLS,
                "city": "جدة",
                "phone": "0555555555",
                "email": "future@example.com",
                "manager_notes": "مدرسة تابعة للمجموعة",
            },
        )
        self.assertRedirects(response, reverse("reports:school_addition_requests"))
        addition_request = SchoolAdditionRequest.objects.get()
        self.assertEqual(addition_request.requested_by, self.manager)
        self.assertEqual(addition_request.source_school, self.source_school)
        self.assertEqual(addition_request.status, SchoolAdditionRequest.Status.APPROVED)
        self.assertIsNone(addition_request.reviewed_by)
        self.assertIsNotNone(addition_request.reviewed_at)
        school = addition_request.created_school
        self.assertIsNotNone(school)
        self.assertTrue(
            SchoolMembership.objects.filter(
                school=school,
                teacher=self.manager,
                role_type=SchoolMembership.RoleType.MANAGER,
                is_active=True,
            ).exists()
        )
        self.assertEqual(SchoolSubscription.objects.get(school=school).plan, self.trial)
        self.assertTrue(SchoolArchiveAddon.objects.get(school=school).is_active)

        page = self.client.get(reverse("reports:school_addition_requests"))
        self.assertContains(page, "مدرسة المستقبل")
        self.assertContains(page, "معتمد")
        self.assertContains(page, "لا تحتاج إلى موافقة مدير المنصة")
        self.assertNotContains(page, "باقة المجموعة")

    def test_duplicate_school_is_not_created_or_recorded(self):
        self._login_manager()
        response = self.client.post(
            reverse("reports:school_addition_requests"),
            {
                "school_name": self.source_school.name,
                "stage": School.Stage.PRIMARY,
                "gender": School.Gender.BOYS,
                "city": self.source_school.city,
            },
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "توجد مدرسة نشطة بالاسم والمدينة نفسيهما")
        self.assertEqual(School.objects.count(), 1)
        self.assertFalse(SchoolAdditionRequest.objects.exists())

    def test_request_page_remains_available_when_current_subscription_expires(self):
        subscription = SchoolSubscription.objects.get(school=self.source_school)
        subscription.end_date = timezone.localdate() - timedelta(days=1)
        subscription.save(update_fields=["end_date", "updated_at"])
        self._login_manager()
        response = self.client.get(reverse("reports:school_addition_requests"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "إضافة مدرسة أخرى")

    def test_removed_group_subscription_url_returns_not_found(self):
        self._login_manager()
        response = self.client.get("/staff/schools/group-subscription/")
        self.assertEqual(response.status_code, 404)

    def test_non_manager_cannot_submit_request(self):
        user = Teacher.objects.create_user(phone="0501112244", name="مستخدم", password="pass")
        self.client.force_login(user)
        response = self.client.get(reverse("reports:school_addition_requests"))
        self.assertEqual(response.status_code, 302)
        self.assertFalse(SchoolAdditionRequest.objects.exists())

    def test_platform_approval_creates_and_links_school_with_trial(self):
        addition_request = SchoolAdditionRequest.objects.create(
            requested_by=self.manager,
            source_school=self.source_school,
            school_name="مدرسة الإبداع",
            stage=School.Stage.HIGH,
            gender=School.Gender.GIRLS,
            city="الدمام",
        )
        self.client.force_login(self.admin)
        response = self.client.post(
            reverse("reports:platform_school_addition_request_review", args=[addition_request.id]),
            {"action": "approve", "review_notes": "تم التحقق"},
        )
        self.assertRedirects(response, reverse("reports:platform_school_addition_requests"))
        addition_request.refresh_from_db()
        self.assertEqual(addition_request.status, SchoolAdditionRequest.Status.APPROVED)
        school = addition_request.created_school
        self.assertIsNotNone(school)
        self.assertTrue(
            SchoolMembership.objects.filter(
                school=school,
                teacher=self.manager,
                role_type=SchoolMembership.RoleType.MANAGER,
                is_active=True,
            ).exists()
        )
        self.assertEqual(SchoolSubscription.objects.get(school=school).plan, self.trial)
        self.assertTrue(SchoolArchiveAddon.objects.get(school=school).is_active)

    def test_rejection_requires_visible_reason(self):
        addition_request = SchoolAdditionRequest.objects.create(
            requested_by=self.manager,
            school_name="مدرسة غير مكتملة",
            stage=School.Stage.PRIMARY,
            gender=School.Gender.BOYS,
        )
        self.client.force_login(self.admin)
        self.client.post(
            reverse("reports:platform_school_addition_request_review", args=[addition_request.id]),
            {"action": "reject", "review_notes": ""},
        )
        addition_request.refresh_from_db()
        self.assertEqual(addition_request.status, SchoolAdditionRequest.Status.PENDING)
        self.client.post(
            reverse("reports:platform_school_addition_request_review", args=[addition_request.id]),
            {"action": "reject", "review_notes": "نحتاج إثبات تبعية المدرسة"},
        )
        addition_request.refresh_from_db()
        self.assertEqual(addition_request.status, SchoolAdditionRequest.Status.REJECTED)
        self.assertEqual(addition_request.review_notes, "نحتاج إثبات تبعية المدرسة")
