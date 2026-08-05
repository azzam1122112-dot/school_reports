"""Storage is sold with the teacher capacity, and quoted before payment.

A flat allowance punishes small schools and starves large ones, so the base
space is derived from the seats a school actually buys. These tests pin both the
arithmetic and the promise the buyer is shown before paying.
"""

from __future__ import annotations

import json

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.flexible_pricing import build_flexible_pricing_catalog
from reports.models import (
    PlatformSettings,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.pricing import SUBSCRIPTION_INCLUDED_FEATURES
from reports.services_archive import (
    school_storage_limit_bytes,
    storage_bytes_for_seats,
    storage_display_for_seats,
)

MB = 1024 * 1024


@override_settings(ALLOWED_HOSTS=["testserver"])
class StorageScalesWithCapacityTests(TestCase):
    def setUp(self):
        settings_obj = PlatformSettings.get_solo()
        settings_obj.storage_mb_per_teacher = 400
        settings_obj.free_storage_mb = 1024
        settings_obj.save(
            update_fields=["storage_mb_per_teacher", "free_storage_mb"]
        )

    def test_the_shipped_default_is_400mb_per_teacher(self):
        field = PlatformSettings._meta.get_field("storage_mb_per_teacher")
        self.assertEqual(field.default, 400)

    def test_storage_grows_in_step_with_the_seats_bought(self):
        for seats in (25, 30, 40, 50, 70, 100):
            self.assertEqual(storage_bytes_for_seats(seats), seats * 400 * MB)

        # Doubling the team doubles the space: the point of the whole model.
        self.assertEqual(
            storage_bytes_for_seats(100),
            storage_bytes_for_seats(50) * 2,
        )

    def test_no_two_capacities_share_the_same_allowance(self):
        allowances = [storage_bytes_for_seats(seats) for seats in (25, 30, 35, 40, 45, 50, 60, 70, 80, 90, 100)]
        self.assertEqual(len(set(allowances)), len(allowances))
        self.assertEqual(allowances, sorted(allowances))

    def test_a_25_teacher_school_clears_a_school_year_of_photos(self):
        """4.9GB under the old 200MB rate was less than one year of reports."""
        self.assertGreaterEqual(storage_bytes_for_seats(25), 9 * 1024 * MB)

    def test_the_live_limit_matches_what_was_quoted(self):
        school = School.objects.create(name="مدرسة التخزين", code="storage-scale")
        plan = SubscriptionPlan.objects.create(
            name="سعة 40", price=500, days_duration=365, max_teachers=40
        )
        SchoolSubscription.objects.create(school=school, plan=plan)
        school.refresh_from_db()

        self.assertEqual(
            school_storage_limit_bytes(school),
            storage_bytes_for_seats(40),
            "الحد المطبّق بعد الشراء يخالف ما عُرض قبله",
        )

    def test_an_expired_subscription_drops_to_the_floor_on_the_local_calendar(self):
        """end_date is written from the local date, so expiry must read it too.

        Comparing against the UTC date left a lapsed school on full seat-based
        storage through the first hours of every Saudi day.
        """
        from datetime import timedelta

        from django.utils import timezone

        school = School.objects.create(name="مدرسة منتهية", code="storage-lapsed")
        plan = SubscriptionPlan.objects.create(
            name="سعة 50", price=100, days_duration=365, max_teachers=50
        )
        subscription = SchoolSubscription.objects.create(school=school, plan=plan)
        subscription.end_date = timezone.localdate() - timedelta(days=1)
        subscription.save(update_fields=["end_date"])
        school.refresh_from_db()

        self.assertTrue(subscription.is_expired)
        self.assertEqual(school_storage_limit_bytes(school), 1024 * MB)

    # ------------------------------------------------------------ the display

    def _seed_anchors(self):
        anchors = {
            30: {25: 149, 50: 229, 100: 379},
            180: {25: 429, 50: 650, 100: 1090},
            365: {25: 790, 50: 1250, 100: 2090},
        }
        for days, prices in anchors.items():
            for capacity, price in prices.items():
                SubscriptionPlan.objects.create(
                    name=f"سعة {capacity} / {days}",
                    price=price,
                    days_duration=days,
                    max_teachers=capacity,
                )

    def test_every_quote_carries_the_storage_it_buys(self):
        self._seed_anchors()

        for entry in build_flexible_pricing_catalog():
            for quote in entry["quotes"]:
                self.assertEqual(
                    quote["storage_bytes"],
                    storage_bytes_for_seats(quote["capacity"]),
                    f"تسعيرة {quote['capacity']} في {entry['key']} بلا مساحة صحيحة",
                )
                self.assertTrue(quote["storage_display"])

    def test_the_landing_calculator_publishes_storage_to_the_browser(self):
        self._seed_anchors()

        response = self.client.get(reverse("reports:landing"))
        html = response.content.decode("utf-8")
        catalog = response.context["flexible_pricing_json"]
        if not isinstance(catalog, dict):
            catalog = json.loads(catalog)

        self.assertIn("data-flex-storage", html)
        quote = next(
            q
            for entry in catalog["periods"]
            if entry["key"] == "1y"
            for q in entry["quotes"]
            if q["capacity"] == 50
        )
        self.assertEqual(quote["storage_display"], storage_display_for_seats(50))

    def test_the_subscription_page_shows_storage_beside_the_price(self):
        school = School.objects.create(name="مدرسة العرض", code="storage-display")
        plan = SubscriptionPlan.objects.create(
            name="تجربة", price=0, days_duration=30, max_teachers=0
        )
        SchoolSubscription.objects.create(school=school, plan=plan)
        self._seed_anchors()
        manager = Teacher.objects.create_user(
            phone="500440001", name="مدير العرض", password="storage-pass", is_staff=True
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
        html = response.content.decode("utf-8")

        self.assertEqual(response.status_code, 200)
        self.assertIn("data-flex-storage", html, "الحاسبة لا تعرض المساحة")
        self.assertIn(
            "data-summary-storage",
            html,
            "ملخص ما قبل الدفع لا يذكر المساحة المشمولة",
        )

    def test_the_included_list_says_storage_is_not_one_size_for_all(self):
        titles = [feature["title"] for feature in SUBSCRIPTION_INCLUDED_FEATURES]
        self.assertIn("مساحة تخزين تكبر مع فريقك", titles)

        entry = next(
            feature
            for feature in SUBSCRIPTION_INCLUDED_FEATURES
            if feature["title"] == "مساحة تخزين تكبر مع فريقك"
        )
        self.assertIn("عدد المعلمين", entry["detail"])
