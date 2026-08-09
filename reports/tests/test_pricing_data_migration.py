from datetime import timedelta
from decimal import Decimal
from importlib import import_module

from django.apps import apps
from django.test import TestCase
from django.utils import timezone

from reports.models import (
    School,
    SchoolArchiveAddon,
    SchoolSubscription,
    SubscriptionPlan,
)


reconcile_paid_plan_entitlements = import_module(
    "reports.migrations.0108_reconcile_paid_plan_entitlements"
).reconcile_paid_plan_entitlements


class PaidPlanEntitlementMigrationTests(TestCase):
    def test_it_preserves_promised_archive_before_normalising_paid_plans(self):
        today = timezone.localdate()
        plan = SubscriptionPlan.objects.create(
            name="باقة قديمة",
            price=Decimal("2990.00"),
            days_duration=365,
            max_teachers=100,
            support_level="standard",
            onboarding_sessions=2,
            included_archive_storage_gb=50,
        )
        school = School.objects.create(name="مدرسة قائمة", code="legacy-paid-plan")
        subscription = SchoolSubscription.objects.create(
            school=school,
            plan=plan,
            start_date=today - timedelta(days=10),
            end_date=today + timedelta(days=355),
            is_active=True,
        )

        reconcile_paid_plan_entitlements(apps, schema_editor=None)

        plan.refresh_from_db()
        self.assertEqual(plan.support_level, "priority")
        self.assertEqual(plan.onboarding_sessions, 0)
        self.assertEqual(plan.included_archive_storage_gb, 0)

        addon = SchoolArchiveAddon.objects.get(school=school)
        self.assertTrue(addon.is_enabled)
        self.assertEqual(addon.start_date, today)
        self.assertEqual(addon.end_date, subscription.end_date)
        self.assertEqual(addon.storage_limit_gb, 50)

    def test_it_does_not_shorten_an_existing_open_ended_archive_addon(self):
        today = timezone.localdate()
        plan = SubscriptionPlan.objects.create(
            name="باقة أرشيف قديمة",
            price=Decimal("1990.00"),
            days_duration=365,
            max_teachers=50,
            included_archive_storage_gb=50,
        )
        school = School.objects.create(name="مدرسة بأرشيف", code="open-archive-addon")
        SchoolSubscription.objects.create(
            school=school,
            plan=plan,
            start_date=today,
            end_date=today + timedelta(days=365),
            is_active=True,
        )
        addon = SchoolArchiveAddon.objects.create(
            school=school,
            is_enabled=False,
            start_date=today,
            end_date=None,
            storage_limit_gb=100,
        )

        reconcile_paid_plan_entitlements(apps, schema_editor=None)

        addon.refresh_from_db()
        self.assertTrue(addon.is_enabled)
        self.assertIsNone(addon.end_date)
        self.assertEqual(addon.storage_limit_gb, 100)
