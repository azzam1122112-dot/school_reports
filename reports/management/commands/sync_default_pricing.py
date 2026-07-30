from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction

from reports.models import ArchiveStorageOption, PlatformSettings, SubscriptionPlan
from reports.pricing import DEFAULT_ARCHIVE_PRICING, DEFAULT_SUBSCRIPTION_PLANS


class Command(BaseCommand):
    help = "Create or update the approved subscription plans and archive pricing."

    def add_arguments(self, parser):
        parser.add_argument(
            "--deactivate-other-plans",
            action="store_true",
            help="Deactivate plans that are not part of the approved pricing matrix.",
        )

    @transaction.atomic
    def handle(self, *args, **options):
        approved_ids: list[int] = []
        created_count = 0
        updated_count = 0

        for spec in DEFAULT_SUBSCRIPTION_PLANS:
            plan = SubscriptionPlan.objects.filter(
                days_duration=spec["days_duration"],
                max_teachers=spec["max_teachers"],
            ).order_by("id").first()

            if plan is None:
                plan = SubscriptionPlan.objects.create(is_active=True, **spec)
                created_count += 1
            else:
                changed = False
                for field, value in spec.items():
                    if getattr(plan, field) != value:
                        setattr(plan, field, value)
                        changed = True
                if not plan.is_active:
                    plan.is_active = True
                    changed = True
                if changed:
                    plan.save(
                        update_fields=[
                            "name",
                            "price",
                            "days_duration",
                            "max_teachers",
                            "description",
                            "is_active",
                        ]
                    )
                    updated_count += 1
            approved_ids.append(plan.id)

        deactivated_count = 0
        if options["deactivate_other_plans"]:
            deactivated_count = (
                SubscriptionPlan.objects.exclude(id__in=approved_ids)
                .filter(is_active=True)
                .update(is_active=False)
            )

        archive = DEFAULT_ARCHIVE_PRICING
        platform_settings = PlatformSettings.get_solo()
        platform_settings.archive_addon_annual_price = archive["annual_price"]
        platform_settings.archive_included_storage_gb = archive["included_storage_gb"]
        platform_settings.archive_storage_block_gb = archive["storage_block_gb"]
        platform_settings.archive_storage_block_price = archive["storage_block_price"]
        platform_settings.free_storage_mb = archive["free_storage_mb"]
        platform_settings.save(
            update_fields=[
                "archive_addon_annual_price",
                "archive_included_storage_gb",
                "archive_storage_block_gb",
                "archive_storage_block_price",
                "free_storage_mb",
            ]
        )

        storage_option = ArchiveStorageOption.objects.filter(
            storage_gb=archive["storage_block_gb"],
        ).order_by("id").first()
        if storage_option is None:
            ArchiveStorageOption.objects.create(
                storage_gb=archive["storage_block_gb"],
                price=archive["storage_block_price"],
                sort_order=10,
                is_active=True,
            )
        else:
            storage_option.price = archive["storage_block_price"]
            storage_option.is_active = True
            storage_option.save(update_fields=["price", "is_active", "updated_at"])

        self.stdout.write(
            self.style.SUCCESS(
                "Pricing synchronized: "
                f"created={created_count}, updated={updated_count}, "
                f"deactivated={deactivated_count}, approved={len(approved_ids)}."
            )
        )
