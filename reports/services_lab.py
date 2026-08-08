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
    rows = (
        LabAssetHandover.objects.filter(school=school)
        .values("asset_id", "asset__name", "person_id", "person_name")
        .annotate(
            out=Sum("quantity", filter=Q(direction=LabAssetHandover.Direction.OUT)),
            back=Sum("quantity", filter=Q(direction=LabAssetHandover.Direction.IN)),
        )
        .order_by("asset__name")
    )
    result = []
    for row in rows:
        remaining = int(row.get("out") or 0) - int(row.get("back") or 0)
        if remaining > 0:
            result.append(
                {
                    "asset_id": row["asset_id"],
                    "asset_name": row["asset__name"],
                    "person_id": row["person_id"],
                    "person_name": row["person_name"] or "—",
                    "quantity": remaining,
                }
            )
    return result


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
