from decimal import Decimal
from io import StringIO

from django.core.management import call_command
from django.test import TestCase

from reports.models import ArchiveStorageOption, PlatformSettings, SubscriptionPlan


class DefaultPricingCommandTests(TestCase):
    def test_sync_creates_the_approved_pricing_matrix_and_archive_prices(self):
        output = StringIO()

        call_command("sync_default_pricing", stdout=output)

        expected = {
            (30, 5): Decimal("0"),
            (30, 25): Decimal("149"),
            (180, 25): Decimal("799"),
            (365, 25): Decimal("1290"),
            (30, 50): Decimal("229"),
            (180, 50): Decimal("1190"),
            (365, 50): Decimal("1990"),
            (30, 100): Decimal("349"),
            (180, 100): Decimal("1790"),
            (365, 100): Decimal("2990"),
        }
        actual = {
            (plan.days_duration, plan.max_teachers): plan.price
            for plan in SubscriptionPlan.objects.filter(is_active=True)
        }

        self.assertEqual(actual, expected)
        self.assertIn("approved=10", output.getvalue())

        # Entitlements are identical across every paid anchor on purpose: prices
        # between the anchors are interpolated, so an entitlement that steps at
        # one anchor creates a band where a school pays more and receives less.
        # Archive storage is sold as an add-on to every capacity instead.
        paid_anchors = SubscriptionPlan.objects.filter(is_active=True, price__gt=0)
        self.assertEqual(
            {
                (
                    plan.support_level,
                    plan.onboarding_sessions,
                    plan.included_archive_storage_gb,
                )
                for plan in paid_anchors
            },
            {("priority", 0, 0)},
        )

        settings_obj = PlatformSettings.get_solo()
        self.assertEqual(settings_obj.archive_addon_annual_price, Decimal("399"))
        self.assertEqual(settings_obj.archive_included_storage_gb, 50)
        self.assertEqual(settings_obj.archive_storage_block_gb, 50)
        self.assertEqual(settings_obj.archive_storage_block_price, Decimal("149"))
        self.assertEqual(settings_obj.free_storage_mb, 1024)

        storage_option = ArchiveStorageOption.objects.get(storage_gb=50)
        self.assertEqual(storage_option.price, Decimal("149"))
        self.assertTrue(storage_option.is_active)

    def test_sync_is_idempotent_and_can_deactivate_legacy_plans(self):
        legacy = SubscriptionPlan.objects.create(
            name="باقة قديمة",
            price=100,
            days_duration=30,
            max_teachers=10,
        )

        call_command("sync_default_pricing", "--deactivate-other-plans")
        call_command("sync_default_pricing", "--deactivate-other-plans")

        self.assertEqual(SubscriptionPlan.objects.count(), 11)
        legacy.refresh_from_db()
        self.assertFalse(legacy.is_active)
