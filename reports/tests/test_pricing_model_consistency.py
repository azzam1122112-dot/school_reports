"""The platform sells by teacher count, not by package.

These tests pin that decision across the three surfaces where money is shown:
the public landing page, the school's own subscription page, and the platform
admin's plans page. A price that differs between them is a support ticket at
best and a refund at worst.
"""

from __future__ import annotations

import json

from itertools import pairwise

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.flexible_pricing import (
    ANCHOR_CAPACITIES,
    FLEXIBLE_CAPACITIES,
    PERIODS,
    build_flexible_pricing_catalog,
    normalize_teacher_capacity,
)
from reports.models import (
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)

# One anchor trio per period, matching the published pricing policy.
ANCHOR_PRICES = {
    "1m": {25: 149, 50: 229, 100: 379},
    "6m": {25: 429, 50: 650, 100: 1090},
    "1y": {25: 790, 50: 1250, 100: 2090},
}


def _seed_anchor_plans() -> None:
    days = {"1m": 30, "6m": 180, "1y": 365}
    for period, prices in ANCHOR_PRICES.items():
        for capacity, price in prices.items():
            SubscriptionPlan.objects.create(
                name=f"سعة {capacity} - {PERIODS[period]['label']}",
                price=price,
                days_duration=days[period],
                max_teachers=capacity,
                description="تشغيل كامل\nملفات إنجاز",
            )


@override_settings(ALLOWED_HOSTS=["testserver"])
class PricingModelConsistencyTests(TestCase):
    def setUp(self):
        _seed_anchor_plans()

    # ------------------------------------------------------------- the model

    def test_every_sellable_capacity_has_a_price_for_all_three_periods(self):
        catalog = build_flexible_pricing_catalog()
        by_period = {entry["key"]: entry for entry in catalog}

        self.assertEqual(set(by_period), set(PERIODS))
        for key, entry in by_period.items():
            priced = {quote["capacity"] for quote in entry["quotes"]}
            missing = set(FLEXIBLE_CAPACITIES) - priced
            self.assertEqual(missing, set(), f"سعات بلا سعر في {key}: {sorted(missing)}")

    def test_price_never_drops_as_capacity_grows(self):
        """A larger capacity that costs less would let a school buy down."""
        for entry in build_flexible_pricing_catalog():
            quotes = sorted(entry["quotes"], key=lambda q: q["capacity"])
            prices = [float(quote["price"]) for quote in quotes]
            self.assertEqual(
                prices,
                sorted(prices),
                f"السعر ينخفض مع زيادة السعة في {entry['key']}",
            )

    def test_a_teacher_count_maps_to_the_smallest_capacity_that_fits(self):
        self.assertEqual(normalize_teacher_capacity(1), FLEXIBLE_CAPACITIES[0])
        self.assertEqual(normalize_teacher_capacity(25), 25)
        self.assertEqual(normalize_teacher_capacity(26), 30)
        self.assertEqual(normalize_teacher_capacity(50), 50)
        self.assertEqual(normalize_teacher_capacity(51), 55)
        self.assertEqual(normalize_teacher_capacity(100), 100)
        self.assertIsNone(normalize_teacher_capacity(101))

    def test_capacity_grows_in_uniform_steps_of_five_from_twenty_five(self):
        """A school paying for 55 must not be pushed up to 60."""
        self.assertEqual(FLEXIBLE_CAPACITIES[0], 25)
        self.assertEqual(FLEXIBLE_CAPACITIES[-1], 100)
        steps = {
            second - first
            for first, second in pairwise(FLEXIBLE_CAPACITIES)
        }
        self.assertEqual(steps, {5}, f"خطوات غير منتظمة: {FLEXIBLE_CAPACITIES}")

    def test_every_five_teacher_step_costs_more_than_the_one_below(self):
        """Each +5 block must carry its own price, never a flat band."""
        for entry in build_flexible_pricing_catalog():
            quotes = sorted(entry["quotes"], key=lambda q: q["capacity"])
            for lower, upper in pairwise(quotes):
                self.assertGreater(
                    float(upper["price"]),
                    float(lower["price"]),
                    f"السعة {upper['capacity']} بنفس سعر {lower['capacity']} في {entry['key']}",
                )

    def test_the_anchors_are_priced_exactly_as_published(self):
        """Interpolation must not move the three numbers the admin maintains."""
        for entry in build_flexible_pricing_catalog():
            quotes = {quote["capacity"]: float(quote["price"]) for quote in entry["quotes"]}
            for capacity in ANCHOR_CAPACITIES:
                self.assertEqual(
                    quotes[capacity],
                    float(ANCHOR_PRICES[entry["key"]][capacity]),
                    f"سعر السعة المرجعية {capacity} تغيّر في {entry['key']}",
                )

    # ---------------------------------------------------------- the surfaces

    @staticmethod
    def _as_catalog(value) -> dict:
        return value if isinstance(value, dict) else json.loads(value)

    def _landing_catalog(self) -> dict:
        response = self.client.get(reverse("reports:landing"))
        self.assertEqual(response.status_code, 200)
        return self._as_catalog(response.context["flexible_pricing_json"])

    def _subscription_catalog(self) -> dict:
        school = School.objects.create(name="مدرسة التسعير", code="pricing-consistency")
        plan = SubscriptionPlan.objects.filter(price=0).first() or SubscriptionPlan.objects.create(
            name="تجربة", price=0, days_duration=30, max_teachers=0
        )
        SchoolSubscription.objects.create(school=school, plan=plan)
        manager = Teacher.objects.create_user(
            phone="500330001", name="مدير التسعير", password="pricing-pass", is_staff=True
        )
        SchoolMembership.objects.create(
            school=school,
            teacher=manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.client.force_login(manager)
        session = self.client.session
        session["active_school_id"] = school.id
        session.save()

        response = self.client.get(reverse("reports:my_subscription"))
        self.assertEqual(response.status_code, 200)
        return self._as_catalog(response.context["flexible_pricing_json"])

    @staticmethod
    def _flatten(catalog: dict) -> dict:
        """(period, capacity) -> price, so the two surfaces compare directly."""
        flat = {}
        for entry in catalog.get("periods") or []:
            for quote in entry.get("quotes") or []:
                flat[(entry["key"], int(quote["capacity"]))] = str(quote["price"])
        return flat

    def test_landing_and_subscription_quote_identical_prices(self):
        landing = self._flatten(self._landing_catalog())
        subscription = self._flatten(self._subscription_catalog())

        self.assertTrue(landing, "صفحة الهبوط لا تنشر أي تسعيرة")
        self.assertEqual(
            landing,
            subscription,
            "سعر مختلف بين صفحة الهبوط وصفحة اشتراك المدرسة",
        )

    def test_landing_sells_by_teacher_count_and_shows_no_package_cards(self):
        response = self.client.get(reverse("reports:landing"))
        html = response.content.decode("utf-8")

        self.assertIn("data-flex-teacher-count", html)
        for period in ("1m", "6m", "1y"):
            self.assertIn(f'data-period="{period}"', html)
        self.assertNotIn("price-card paid-card", html)

    def test_admin_plans_page_states_it_maintains_reference_anchors(self):
        admin = Teacher.objects.create_user(
            phone="500330002",
            name="مالك النظام",
            password="pricing-pass",
            is_staff=True,
            is_superuser=True,
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("reports:platform_plans_list"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode("utf-8")

        # The customer sees a calculator, so the admin page must not claim these
        # cards are what the customer is shown.
        self.assertNotIn(
            "هذه هي الباقات نفسها الظاهرة في الصفحة الرئيسية وصفحة إدارة الاشتراك",
            html,
        )
        self.assertIn("عدد المعلمين", html)
