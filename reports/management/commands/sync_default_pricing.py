from __future__ import annotations

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from reports.models import (
    ArchiveStorageOption,
    PlatformSettings,
    SchoolArchiveAddon,
    SchoolSubscription,
    SubscriptionPlan,
)
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
                            "support_level",
                            "onboarding_sessions",
                            "included_archive_storage_gb",
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

        # باقة افتراضية لكل مساحة على حدة. المساحتان تُفرضان بحدّين منفصلين،
        # فبقاء إحداهما بلا منتج يعني مدرسةً تُمنع من العمل ولا تجد ما تشتريه.
        for bucket in (
            ArchiveStorageOption.Bucket.WORK,
            ArchiveStorageOption.Bucket.ARCHIVE,
        ):
            storage_option = ArchiveStorageOption.objects.filter(
                bucket=bucket,
                storage_gb=archive["storage_block_gb"],
            ).order_by("id").first()
            if storage_option is None:
                ArchiveStorageOption.objects.create(
                    bucket=bucket,
                    storage_gb=archive["storage_block_gb"],
                    price=archive["storage_block_price"],
                    sort_order=10,
                    is_active=True,
                )
            else:
                storage_option.price = archive["storage_block_price"]
                storage_option.is_active = True
                storage_option.save(update_fields=["price", "is_active", "updated_at"])

        included_archive_count = 0
        today = timezone.localdate()
        eligible_subscriptions = SchoolSubscription.objects.select_related("plan").filter(
            is_active=True,
            end_date__gte=today,
            plan__included_archive_storage_gb__gt=0,
        )
        for subscription in eligible_subscriptions:
            included_gb = int(subscription.plan.included_archive_storage_gb or 0)
            addon, created = SchoolArchiveAddon.objects.get_or_create(
                school=subscription.school,
                defaults={
                    "is_enabled": True,
                    "start_date": max(subscription.start_date, today),
                    "end_date": subscription.end_date,
                    "storage_limit_gb": included_gb,
                    "paid_amount": 0,
                    "notes": f"مشمولة تلقائياً ضمن باقة {subscription.plan.name}.",
                },
            )
            if not created:
                addon.is_enabled = True
                addon.end_date = max(addon.end_date or subscription.end_date, subscription.end_date)
                addon.storage_limit_gb = max(int(addon.storage_limit_gb or 0), included_gb)
                addon.save(update_fields=["is_enabled", "end_date", "storage_limit_gb", "updated_at"])
            included_archive_count += 1

        self.stdout.write(
            self.style.SUCCESS(
                "Pricing synchronized: "
                f"created={created_count}, updated={updated_count}, "
                f"deactivated={deactivated_count}, approved={len(approved_ids)}, "
                f"included_archive={included_archive_count}."
            )
        )
