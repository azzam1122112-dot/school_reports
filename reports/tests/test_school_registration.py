from __future__ import annotations

from datetime import timedelta

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    School,
    SchoolArchiveAddon,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    RATELIMIT_ENABLE=False,
    TRIAL_DAYS=30,
)
class SchoolRegistrationFlowTests(TestCase):
    def registration_payload(self, **overrides):
        payload = {
            "school_name": "مدرسة التجربة المتكاملة",
            "stage": School.Stage.PRIMARY,
            "gender": School.Gender.BOYS,
            "city": "الرياض",
            "manager_name": "مدير المدرسة",
            "manager_phone": "+966 55 123 4567",
            "manager_email": "manager@example.edu.sa",
            "password": "FreeTrial#2026",
            "password_confirm": "FreeTrial#2026",
            "accept_policies": "on",
        }
        payload.update(overrides)
        return payload

    def test_registration_page_explains_full_trial_before_submission(self):
        response = self.client.get(reverse("reports:register_school"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "تجربة كاملة")
        self.assertContains(response, "أرشيف تجريبي")
        self.assertContains(response, "بيانات الدخول بوضوح")
        self.assertContains(response, 'autocomplete="tel"')
        self.assertContains(response, 'autocomplete="new-password"', count=2)

    def test_registration_provisions_full_trial_and_shows_credentials_once(self):
        response = self.client.post(
            reverse("reports:register_school"),
            self.registration_payload(),
        )

        self.assertRedirects(
            response,
            reverse("reports:registration_success"),
            fetch_redirect_response=False,
        )

        school = School.objects.get(name="مدرسة التجربة المتكاملة")
        manager = Teacher.objects.get(phone="0551234567")
        membership = SchoolMembership.objects.get(school=school, teacher=manager)
        subscription = SchoolSubscription.objects.select_related("plan").get(school=school)
        archive_addon = SchoolArchiveAddon.objects.get(school=school)

        self.assertEqual(membership.role_type, SchoolMembership.RoleType.MANAGER)
        self.assertTrue(membership.is_active)
        self.assertTrue(manager.check_password("FreeTrial#2026"))
        self.assertEqual(manager.email, "manager@example.edu.sa")
        self.assertNotIn("FreeTrial#2026", manager.password)
        self.assertEqual(subscription.plan.price, 0)
        self.assertTrue(subscription.plan.is_active)
        self.assertEqual(subscription.plan.max_teachers, 5)
        self.assertEqual(subscription.plan.days_duration, 30)
        self.assertEqual(
            subscription.end_date,
            subscription.start_date + timedelta(days=subscription.plan.days_duration - 1),
        )
        self.assertTrue(archive_addon.is_active)
        self.assertEqual(archive_addon.end_date, subscription.end_date)
        self.assertEqual(archive_addon.storage_limit_gb, 1)
        self.assertEqual(self.client.session["active_school_id"], school.id)

        success = self.client.get(reverse("reports:registration_success"))
        self.assertEqual(success.status_code, 200)
        self.assertContains(success, "0551234567")
        self.assertContains(success, "FreeTrial#2026")
        self.assertContains(success, "تظهر في هذه الصفحة مرة واحدة")
        self.assertContains(success, "التجربة الكاملة مفعّلة الآن")
        self.assertIn("no-cache", success.headers["Cache-Control"])

        self.assertNotIn("school_registration_receipt", self.client.session)
        second_visit = self.client.get(reverse("reports:registration_success"))
        self.assertRedirects(
            second_visit,
            reverse("reports:admin_dashboard"),
            fetch_redirect_response=False,
        )

        archive_page = self.client.get(reverse("reports:school_archive"))
        self.assertEqual(archive_page.status_code, 200)

    def test_invalid_phone_does_not_create_partial_school_data(self):
        response = self.client.post(
            reverse("reports:register_school"),
            self.registration_payload(manager_phone="12345"),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "أدخل رقم جوال سعودي صحيحًا")
        self.assertFalse(School.objects.exists())
        self.assertFalse(Teacher.objects.exists())
        self.assertFalse(SubscriptionPlan.objects.exists())

    def test_missing_manager_email_rejects_registration(self):
        response = self.client.post(
            reverse("reports:register_school"),
            self.registration_payload(manager_email=""),
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "هذا الحقل مطلوب")
        self.assertFalse(School.objects.exists())
        self.assertFalse(Teacher.objects.exists())

    def test_registration_preserves_first_touch_marketing_attribution(self):
        self.client.get(
            reverse("reports:register_school"),
            {
                "utm_source": "meta",
                "utm_medium": "paid_social",
                "utm_campaign": "schools_launch",
                "utm_content": "principal_video",
                "utm_term": "school_reports",
                "fbclid": "test-click-id",
            },
            HTTP_REFERER="https://www.facebook.com/campaign/example",
        )

        response = self.client.post(
            reverse("reports:register_school"),
            self.registration_payload(manager_phone="0559876543"),
        )

        self.assertEqual(response.status_code, 302)
        school = School.objects.get(name="مدرسة التجربة المتكاملة")
        self.assertEqual(school.marketing_source, "meta")
        self.assertEqual(school.marketing_medium, "paid_social")
        self.assertEqual(school.marketing_campaign, "schools_launch")
        self.assertEqual(school.marketing_content, "principal_video")
        self.assertEqual(school.marketing_term, "school_reports")
        self.assertEqual(school.marketing_click_id, "fbclid:test-click-id")
        self.assertEqual(school.marketing_referrer, "www.facebook.com")
