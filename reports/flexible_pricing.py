from __future__ import annotations

from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable


# السعة تُباع بخطوات ثابتة من 5 معلمين ابتداءً من 25، فكل زيادة 5 لها سعرها.
# الخطوة موحّدة على المدى كله: قفزات العشرة السابقة فوق الخمسين كانت تجبر
# مدرسة تحتاج 55 معلماً على شراء 60.
CAPACITY_STEP = 5
MIN_CAPACITY = 25
MAX_CAPACITY = 100

FLEXIBLE_CAPACITIES = tuple(range(MIN_CAPACITY, MAX_CAPACITY + 1, CAPACITY_STEP))

# The only capacities stored as plans. Everything between them is interpolated,
# so these are the nine numbers the platform admin actually maintains.
ANCHOR_CAPACITIES = (25, 50, 100)

PERIODS = {
    "1m": {"label": "شهري", "months": 1, "target_days": 30, "sort": 10},
    "6m": {"label": "6 أشهر", "months": 6, "target_days": 180, "sort": 20},
    "1y": {"label": "سنة", "months": 12, "target_days": 365, "sort": 30},
}


def period_key_for_days(days: int) -> str | None:
    days = int(days or 0)
    if 20 <= days <= 44:
        return "1m"
    if 160 <= days <= 210:
        return "6m"
    if 330 <= days <= 400:
        return "1y"
    return None


def normalize_teacher_capacity(teacher_count: int) -> int | None:
    """Return the smallest sellable capacity that can contain ``teacher_count``."""
    count = max(int(teacher_count or 0), 1)
    for capacity in FLEXIBLE_CAPACITIES:
        if count <= capacity:
            return capacity
    return None


def _active_paid_plans(plans: Iterable | None = None) -> list:
    if plans is None:
        from .models import SubscriptionPlan

        plans = SubscriptionPlan.objects.filter(is_active=True, price__gt=0)
    return [
        plan
        for plan in plans
        if bool(getattr(plan, "is_active", True))
        and Decimal(getattr(plan, "price", 0) or 0) > 0
        and int(getattr(plan, "max_teachers", 0) or 0) > 0
        and period_key_for_days(getattr(plan, "days_duration", 0))
    ]


def _period_anchors(plans: Iterable | None = None) -> dict[str, list]:
    grouped: dict[str, dict[int, object]] = {key: {} for key in PERIODS}
    for plan in _active_paid_plans(plans):
        period_key = period_key_for_days(plan.days_duration)
        if period_key is None:
            continue
        capacity = int(plan.max_teachers or 0)
        existing = grouped[period_key].get(capacity)
        target_days = PERIODS[period_key]["target_days"]
        if existing is None or (
            abs(int(plan.days_duration or 0) - target_days),
            Decimal(plan.price),
            int(plan.pk or 0),
        ) < (
            abs(int(existing.days_duration or 0) - target_days),
            Decimal(existing.price),
            int(existing.pk or 0),
        ):
            grouped[period_key][capacity] = plan
    return {
        key: sorted(capacity_map.values(), key=lambda plan: (plan.max_teachers, plan.pk or 0))
        for key, capacity_map in grouped.items()
        if capacity_map
    }


def _interpolated_quote(anchors: list, capacity: int, storage_mb_per_teacher: int = 0) -> dict | None:
    exact = next((plan for plan in anchors if int(plan.max_teachers) == capacity), None)
    if exact is not None:
        price = Decimal(exact.price)
        plan = exact
    else:
        lower = None
        upper = None
        for anchor in anchors:
            anchor_capacity = int(anchor.max_teachers or 0)
            if anchor_capacity < capacity:
                lower = anchor
            elif anchor_capacity > capacity:
                upper = anchor
                break
        if lower is None or upper is None:
            return None
        lower_capacity = Decimal(int(lower.max_teachers))
        upper_capacity = Decimal(int(upper.max_teachers))
        position = (Decimal(capacity) - lower_capacity) / (upper_capacity - lower_capacity)
        price = Decimal(lower.price) + ((Decimal(upper.price) - Decimal(lower.price)) * position)
        price = price.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        plan = lower

    period_key = period_key_for_days(plan.days_duration)
    months = int(PERIODS[period_key]["months"])
    monthly = (price / Decimal(months)).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    # Storage is part of what the capacity buys, so it is quoted with the price
    # rather than discovered after payment. The rate is passed in: reading it per
    # quote would add a settings query for every row on the landing page.
    from .services_archive import _human_size

    storage_bytes = max(0, int(capacity)) * max(0, int(storage_mb_per_teacher or 0)) * 1024 * 1024

    return {
        "capacity": capacity,
        "price": price,
        "price_display": f"{price:,.0f}",
        "monthly_equivalent": monthly,
        "monthly_equivalent_display": f"{monthly:,.0f}",
        "storage_bytes": storage_bytes,
        "storage_display": _human_size(storage_bytes),
        "plan": plan,
        "plan_id": plan.pk,
        "plan_name": plan.name,
        "spare_example": max(capacity - 27, 0),
    }


def build_flexible_pricing_catalog(plans: Iterable | None = None) -> list[dict]:
    """Build customer-facing quotes from the active 25/50/100 price anchors.

    Existing plans remain the commercial source of truth. Intermediate capacities
    are interpolated, so editing an anchor price in the platform dashboard updates
    the landing page and checkout without creating dozens of database plans.
    """
    from .services_archive import _storage_mb_per_teacher

    storage_rate = _storage_mb_per_teacher()

    groups = []
    for period_key, anchors in _period_anchors(plans).items():
        if not anchors:
            continue
        min_capacity = int(anchors[0].max_teachers)
        max_capacity = int(anchors[-1].max_teachers)
        target_capacities = sorted(
            {
                *(
                    capacity
                    for capacity in FLEXIBLE_CAPACITIES
                    if min_capacity <= capacity <= max_capacity
                ),
                *(int(anchor.max_teachers) for anchor in anchors),
            }
        )
        quotes = [
            quote
            for capacity in target_capacities
            if (quote := _interpolated_quote(anchors, capacity, storage_rate)) is not None
        ]
        if not quotes:
            continue
        meta = PERIODS[period_key]
        groups.append(
            {
                "key": period_key,
                "label": meta["label"],
                "months": meta["months"],
                "sort": meta["sort"],
                "quotes": quotes,
                "min_capacity": min(quote["capacity"] for quote in quotes),
                "max_capacity": max(quote["capacity"] for quote in quotes),
            }
        )
    return sorted(groups, key=lambda group: group["sort"])


def serialize_flexible_pricing_catalog(catalog: list[dict]) -> dict:
    return {
        "capacities": list(FLEXIBLE_CAPACITIES),
        "periods": [
            {
                "key": group["key"],
                "label": group["label"],
                "months": group["months"],
                "quotes": [
                    {
                        "capacity": quote["capacity"],
                        "price": float(quote["price"]),
                        "price_display": quote["price_display"],
                        "monthly_equivalent": float(quote["monthly_equivalent"]),
                        "monthly_equivalent_display": quote["monthly_equivalent_display"],
                        "storage_bytes": int(quote["storage_bytes"]),
                        "storage_display": quote["storage_display"],
                        "plan_id": quote["plan_id"],
                        "plan_name": quote["plan_name"],
                    }
                    for quote in group["quotes"]
                ],
            }
            for group in catalog
        ],
    }


def quote_for_selection(plan_id: int, requested_capacity: int) -> dict | None:
    from .models import SubscriptionPlan

    selected_plan = SubscriptionPlan.objects.filter(
        pk=plan_id,
        is_active=True,
        price__gt=0,
    ).first()
    if selected_plan is None:
        return None
    period_key = period_key_for_days(selected_plan.days_duration)
    if period_key is None:
        return None
    capacity = normalize_teacher_capacity(requested_capacity)
    if capacity is None:
        return None
    group = next(
        (item for item in build_flexible_pricing_catalog() if item["key"] == period_key),
        None,
    )
    if group is None:
        return None
    return next(
        (quote for quote in group["quotes"] if quote["capacity"] == capacity),
        None,
    )
