from django.db import migrations
from django.utils import timezone


def reconcile_paid_plan_entitlements(apps, schema_editor):
    """Make stored paid anchors match the uniform commercial model.

    The approved pricing matrix stopped bundling archive storage and onboarding
    at individual capacity anchors, but changing ``reports.pricing`` does not
    update rows that already exist in production. Preserve any archive benefit
    already promised by materialising it as a school add-on before normalising
    the plans themselves.
    """

    SubscriptionPlan = apps.get_model("reports", "SubscriptionPlan")
    SchoolSubscription = apps.get_model("reports", "SchoolSubscription")
    SchoolArchiveAddon = apps.get_model("reports", "SchoolArchiveAddon")

    today = timezone.localdate()
    subscriptions = SchoolSubscription.objects.filter(
        is_active=True,
        end_date__gte=today,
        plan__price__gt=0,
        plan__included_archive_storage_gb__gt=0,
    ).select_related("plan")

    for subscription in subscriptions.iterator():
        included_gb = int(subscription.plan.included_archive_storage_gb or 0)
        addon, created = SchoolArchiveAddon.objects.get_or_create(
            school_id=subscription.school_id,
            defaults={
                "is_enabled": True,
                "start_date": max(subscription.start_date, today),
                "end_date": subscription.end_date,
                "storage_limit_gb": included_gb,
                "paid_amount": 0,
                "notes": (
                    "حُفظت تلقائياً من استحقاق الأرشيف في الباقة السابقة أثناء "
                    "توحيد نموذج التسعير."
                ),
            },
        )
        if created:
            continue

        addon.is_enabled = True
        if addon.end_date is not None:
            addon.end_date = max(addon.end_date, subscription.end_date)
        addon.storage_limit_gb = max(int(addon.storage_limit_gb or 0), included_gb)
        addon.save(update_fields=["is_enabled", "end_date", "storage_limit_gb"])

    SubscriptionPlan.objects.filter(price__gt=0).update(
        support_level="priority",
        onboarding_sessions=0,
        included_archive_storage_gb=0,
    )


class Migration(migrations.Migration):

    dependencies = [
        ("reports", "0107_payment_payer_fields"),
    ]

    operations = [
        migrations.RunPython(
            reconcile_paid_plan_entitlements,
            reverse_code=migrations.RunPython.noop,
        ),
    ]
