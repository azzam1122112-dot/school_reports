from __future__ import annotations

import re

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import SubscriptionPlan


@override_settings(ALLOWED_HOSTS=["testserver"], TRIAL_DAYS=21)
class LandingPageTests(TestCase):
    def test_landing_explains_the_product_and_has_a_clear_primary_action(self):
        response = self.client.get(reverse("reports:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "إدارة المدرسة")
        self.assertContains(response, "تبدأ من صورة واضحة")
        self.assertContains(response, "ملفات إنجاز المعلمين")
        self.assertContains(response, "التعاميم")
        self.assertContains(response, "الأرشيف")
        self.assertContains(response, "ابدأ تجربة مجانية 21 يوم")
        self.assertContains(response, reverse("reports:register_school"))
        self.assertContains(response, "img/landing/dashboard-live.webp")
        self.assertContains(response, "img/brand-mark.svg")
        self.assertNotContains(response, "+500K")
        self.assertNotContains(response, "100% رضا")

    def test_landing_has_accessible_navigation_and_single_main_heading(self):
        response = self.client.get(reverse("reports:landing"))
        html = response.content.decode("utf-8")

        self.assertEqual(len(re.findall(r"<h1\b", html, re.IGNORECASE)), 1)
        self.assertIn('href="#mainContent"', html)
        self.assertIn('aria-controls="mobileMenu"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn('id="security"', html)
        self.assertNotIn('href="/terms/', html)

    def test_active_plans_drive_the_pricing_cards_and_period_switch(self):
        SubscriptionPlan.objects.create(
            name="تجربة المدرسة",
            price=0,
            days_duration=21,
            max_teachers=5,
            description="تشغيل كامل للتجربة\nدعم البدء",
        )
        SubscriptionPlan.objects.create(
            name="مدرسة متوسطة",
            price=650,
            days_duration=180,
            max_teachers=50,
            description="تقارير غير محدودة\nملفات إنجاز",
        )
        SubscriptionPlan.objects.create(
            name="مدرسة متوسطة سنوي",
            price=1250,
            days_duration=365,
            max_teachers=50,
            description="تقارير غير محدودة\nملفات إنجاز",
        )

        response = self.client.get(reverse("reports:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["pricing_cards"]), 1)
        self.assertContains(response, 'data-period="6m"')
        self.assertContains(response, 'data-period="1y"')
        self.assertContains(response, "650")
        self.assertContains(response, "1,250")
        self.assertContains(response, "حتى 50 معلماً")
        self.assertContains(response, "تشغيل كامل للتجربة")

    def test_inactive_plans_are_not_advertised(self):
        SubscriptionPlan.objects.create(
            name="باقة قديمة لا تظهر",
            price=999,
            days_duration=365,
            max_teachers=20,
            is_active=False,
        )

        response = self.client.get(reverse("reports:landing"))

        self.assertNotContains(response, "باقة قديمة لا تظهر")
        self.assertEqual(response.context["pricing_cards"], [])
