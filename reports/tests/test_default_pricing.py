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
            (14, 5): Decimal("0"),
            (180, 25): Decimal("499"),
            (365, 25): Decimal("849"),
            (180, 50): Decimal("649"),
            (365, 50): Decimal("1099"),
            (180, 100): Decimal("899"),
            (365, 100): Decimal("1499"),
        }
        actual = {
            (plan.days_duration, plan.max_teachers): plan.price
            for plan in SubscriptionPlan.objects.filter(is_active=True)
        }

        self.assertEqual(actual, expected)
        self.assertIn("approved=7", output.getvalue())

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

        self.assertEqual(SubscriptionPlan.objects.count(), 8)
        legacy.refresh_from_db()
        self.assertFalse(legacy.is_active)
