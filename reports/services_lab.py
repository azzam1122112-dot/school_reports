# -*- coding: utf-8 -*-
"""خدمة المختبر: العهدة وحركتها والتجارب.

**كل كتابة تمرّ من هنا.** الشاشة تترجم النقرة إلى نداء، والقاعدةُ الوحيدة التي
تُنفَّذ في العرض هي «هل يملك هذا المستخدم الوصول». وما دون ذلك — حساب المتاح،
وقيد الحركة، وتغيير الحالة — يعيش هنا، لأن التحقق الذي يسكن في عرضٍ يُنسى عند
إضافة مسار ثانٍ للشيء نفسه.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Count, Q, Sum
from django.utils import timezone

from .model_parts.approvals import ApprovalState, PENDING_REVIEW_STATES
from .models import LabAsset, LabAssetHandover, LabExperiment

__all__ = [
    "assets_for_school",
    "experiments_for_school",
    "handovers_for_school",
    "lab_summary",
    "record_handover",
    "set_asset_condition",
    "outstanding_handovers",
]


# ─────────────────────────────────────────────────────────────────────────────
# استعلامات العرض
# ─────────────────────────────────────────────────────────────────────────────
def assets_for_school(school, *, include_inactive: bool = False):
    """جرد المدرسة، ومعه ما تحتاجه الشاشة في استعلام واحد.

    الكمية الخارجة تُجمَّع في الاستعلام لا في حلقة: قراءة ``out_quantity`` لكل
    صفّ في كشفٍ من مئة صنف مئةُ استعلام — وهو ما لا يظهر في مختبرٍ فيه عشرة
    أصناف ويظهر بعد عام من الاستعمال.
    """
    qs = LabAsset.objects.filter(school=school).select_related("custodian")
    if not include_inactive:
        qs = qs.filter(is_active=True)
    return qs.annotate(
        handed_out=Sum(
            "handovers__quantity",
            filter=Q(handovers__direction=LabAssetHandover.Direction.OUT),
        ),
        handed_back=Sum(
            "handovers__quantity",
            filter=Q(handovers__direction=LabAssetHandover.Direction.IN),
        ),
    ).order_by("name", "id")


def experiments_for_school(school):
    return (
        LabExperiment.objects.filter(school=school)
        .select_related("recorder", "requested_by", "report")
        .prefetch_related("assets")
        .order_by("-experiment_date", "-id")
    )


def handovers_for_school(school, *, limit: int | None = None):
    qs = (
        LabAssetHandover.objects.filter(school=school)
        .select_related("asset", "person", "recorded_by")
        .order_by("-happened_at", "-id")
    )
    return qs[:limit] if limit else qs


def outstanding_handovers(school):
    """ما خرج من المختبر ولم يُرجَع — مجموعاً بالصنف والمستلم.

    السؤال العملي عند الجرد ليس «كم حركةً وقعت؟» بل «ماذا لا يزال خارج
    المختبر وعند من؟»، وهو فرقٌ لا يجيبه سردُ الحركات بترتيب زمني.
    """
    # الحركات القديمة كانت تسمح بإرجاعٍ بلا اسم. التجميع بـ ``person_id``
    # يجعل هذا الإرجاع في مجموعة مستقلة سالبة، فيبقى المستلم ظاهراً في كشف
    # المتأخرات رغم عودة الصنف فعلياً. نبني رصيداً زمنياً (FIFO) لكل صنف:
    # الإرجاع المسمّى يسوّي عهدة الشخص أولاً، وغير المسمّى يسوّي أقدم عهدة.
    movements = (
        LabAssetHandover.objects.filter(school=school)
        .values(
            "asset_id",
            "asset__name",
            "person_id",
            "person_name",
            "direction",
            "quantity",
        )
        .order_by("asset_id", "happened_at", "id")
    )
    lots_by_asset: dict[int, list[dict]] = {}
    asset_names: dict[int, str] = {}

    for movement in movements:
        asset_id = movement["asset_id"]
        asset_names[asset_id] = movement["asset__name"]
        lots = lots_by_asset.setdefault(asset_id, [])
        quantity = int(movement.get("quantity") or 0)

        if movement["direction"] == LabAssetHandover.Direction.OUT:
            lots.append(
                {
                    "person_id": movement["person_id"],
                    "person_name": movement["person_name"] or "—",
                    "quantity": quantity,
                }
            )
            continue

        # المسمّى يطابق صاحبه أولاً. ويأتي بقية الرصيد بعده لإبقاء الإجمالي
        # متسقاً حتى مع بيانات تاريخية غير مكتملة أو حساب حُذف لاحقاً.
        person_id = movement["person_id"]
        person_name = movement["person_name"] or ""

        def same_person(lot):
            if person_id is not None:
                return lot["person_id"] == person_id
            return bool(person_name) and lot["person_name"] == person_name

        candidates = (
            [lot for lot in lots if same_person(lot)]
            + [lot for lot in lots if not same_person(lot)]
            if person_id is not None or person_name
            else list(lots)
        )
        remaining = quantity
        for lot in candidates:
            if remaining <= 0:
                break
            consumed = min(remaining, lot["quantity"])
            lot["quantity"] -= consumed
            remaining -= consumed

    result = []
    for asset_id, lots in lots_by_asset.items():
        grouped: dict[tuple[int | None, str], int] = {}
        for lot in lots:
            if lot["quantity"] <= 0:
                continue
            key = (lot["person_id"], lot["person_name"])
            grouped[key] = grouped.get(key, 0) + lot["quantity"]
        for (person_id, person_name), quantity in grouped.items():
            result.append(
                {
                    "asset_id": asset_id,
                    "asset_name": asset_names[asset_id],
                    "person_id": person_id,
                    "person_name": person_name,
                    "quantity": quantity,
                }
            )
    return sorted(result, key=lambda row: (row["asset_name"], row["person_name"]))


def lab_summary(school) -> dict:
    """مؤشرات المختبر — في استعلامين لا استعلامٍ لكل رقم."""
    assets = LabAsset.objects.filter(school=school, is_active=True).aggregate(
        total=Count("id"),
        attention=Count(
            "id", filter=Q(condition__in=LabAsset.ATTENTION_CONDITIONS)
        ),
        missing=Count("id", filter=Q(condition=LabAsset.Condition.MISSING)),
        damaged=Count("id", filter=Q(condition=LabAsset.Condition.DAMAGED)),
    )
    experiments = LabExperiment.objects.filter(school=school).aggregate(
        total=Count("id"),
        pending=Count("id", filter=Q(approval_state__in=PENDING_REVIEW_STATES)),
        approved=Count("id", filter=Q(approval_state=ApprovalState.APPROVED)),
        drafts=Count("id", filter=Q(approval_state=ApprovalState.DRAFT)),
    )
    return {
        "assets_total": int(assets.get("total") or 0),
        "assets_attention": int(assets.get("attention") or 0),
        "assets_missing": int(assets.get("missing") or 0),
        "assets_damaged": int(assets.get("damaged") or 0),
        "experiments_total": int(experiments.get("total") or 0),
        "experiments_pending": int(experiments.get("pending") or 0),
        "experiments_approved": int(experiments.get("approved") or 0),
        "experiments_drafts": int(experiments.get("drafts") or 0),
        "outstanding": len(outstanding_handovers(school)),
    }


# ─────────────────────────────────────────────────────────────────────────────
# الكتابة
# ─────────────────────────────────────────────────────────────────────────────
@transaction.atomic
def record_handover(
    asset: LabAsset,
    *,
    direction: str,
    person,
    quantity: int,
    actor,
    happened_at=None,
    note: str = "",
) -> LabAssetHandover:
    """قيدُ حركةِ عهدة، بعد التحقق من أنها ممكنة.

    ``full_clean`` تُستدعى صراحةً: قواعد «لا يُسلَّم أكثر من المتاح» و«لا يُرجَع
    أكثر من الخارج» تعيش في ``clean`` النموذج، و``save`` وحدها لا تستدعيها —
    فمسارٌ يتخطاها يكتب جرداً يقول إن خارج المختبر خمساً من أربع.
    """
    if person is None:
        if direction == LabAssetHandover.Direction.OUT:
            raise ValidationError("حدّد من تسلَّم الصنف.")
        if direction == LabAssetHandover.Direction.IN:
            raise ValidationError("حدّد من أعاد الصنف لتسوية عهدته بدقة.")

    handover = LabAssetHandover(
        school=asset.school,
        asset=asset,
        direction=direction,
        person=person,
        quantity=int(quantity or 0),
        happened_at=happened_at or timezone.now(),
        recorded_by=actor,
        note=(note or "").strip()[:255],
    )
    handover.full_clean(exclude=["person_name", "school"])
    handover.save()
    return handover


@transaction.atomic
def set_asset_condition(asset: LabAsset, *, condition: str, actor=None) -> LabAsset:
    """تغيير حالة صنف — تالفاً أو مفقوداً أو سليماً بعد الصيانة."""
    valid = {value for value, _label in LabAsset.Condition.choices}
    if condition not in valid:
        raise ValidationError("حالة غير معروفة.")

    # صنفٌ لا يزال بعضُه خارج المختبر لا يُوسَم مفقوداً: الفقد حكمٌ على ما في
    # المختبر، وما هو في يد معلّمٍ معروفٍ ليس مفقوداً — هو مُسلَّم.
    if condition == LabAsset.Condition.MISSING and asset.out_quantity > 0:
        raise ValidationError(
            "لا يُوسَم الصنف مفقوداً وبعضه مُسلَّم — سجّل الإرجاع أولاً أو راجع سجل الحركة."
        )

    asset.condition = condition
    asset.save(update_fields=["condition", "updated_at"])
    return asset
