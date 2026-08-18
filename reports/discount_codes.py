# reports/discount_codes.py
# -*- coding: utf-8 -*-
"""منطق أكواد الخصم: التحقق، الحساب، الحجز الذرّي، والتحرير.

**التوقيت هو جوهر هذه الوحدة.** الاستخدام يُحجز لحظة إنشاء طلب الدفع لا لحظة
اعتماده: كودٌ بعشرة استخدامات لا يجوز أن تدخل به ثلاثون مدرسة طلباتٍ «قيد
المراجعة» معاً ثم تُعتمد كلها. وبالمقابل، طلبٌ رُفض أو أُلغي قبل تطبيق أثره
يحرّر حجزه فيعود الاستخدام إلى الرصيد.

الحجز يجري تحت قفل صف الكود (``select_for_update``) وعدُّ الاستخدامات يُقرأ من
سجلات ``DiscountRedemption`` نفسها — لا من عدّاد مخزّن — فلا انحراف بين العدّ
والواقع. وقاعدة «مرة واحدة لكل مدرسة» يضمنها قيدٌ فريد في قاعدة البيانات حتى
لو تسابق طلبان.
"""
from __future__ import annotations

from decimal import Decimal

from django.db import IntegrityError, transaction
from django.utils import timezone

from .models import DiscountCode, DiscountRedemption, Payment

MSG_INVALID = "كود الخصم غير صحيح أو غير متاح."
MSG_NOT_STARTED = "كود الخصم غير متاح بعد."
MSG_EXPIRED = "انتهت صلاحية كود الخصم."
MSG_EXHAUSTED = "انتهت جميع استخدامات هذا الكود."
MSG_ALREADY_USED = "سبق لمدرستك استخدام هذا الكود؛ الكود متاح لكل مدرسة مرة واحدة."


class DiscountCodeError(Exception):
    """خطأ يوقف تطبيق كود الخصم برسالة صالحة للعرض للمستخدم."""


def normalize_code(raw: str) -> str:
    return (raw or "").strip().upper()


def _check_usable(code: DiscountCode, school) -> None:
    """يتحقق من صلاحية الكود لهذه المدرسة الآن، ويرمي ``DiscountCodeError``.

    ترتيب الفحوص مقصود: أخصُّ رسالة تُقال أولاً — فمدرسة استخدمت كوداً نُفدت
    استخداماته تُخبر أنها استخدمته، لا أن الكود انتهى.
    """
    today = timezone.localdate()
    if not code.is_active:
        raise DiscountCodeError(MSG_INVALID)
    if code.valid_from and today < code.valid_from:
        raise DiscountCodeError(MSG_NOT_STARTED)
    if code.valid_until and today > code.valid_until:
        raise DiscountCodeError(MSG_EXPIRED)
    if DiscountRedemption.objects.filter(code=code, school=school).exists():
        raise DiscountCodeError(MSG_ALREADY_USED)
    if DiscountRedemption.objects.filter(code=code).count() >= int(code.max_uses or 0):
        raise DiscountCodeError(MSG_EXHAUSTED)


def find_usable_code(raw_code: str, school) -> DiscountCode:
    """يعيد كود الخصم إن كان قابلاً للاستخدام لهذه المدرسة، وإلا يرمي خطأً برسالته."""
    normalized = normalize_code(raw_code)
    if not normalized:
        raise DiscountCodeError(MSG_INVALID)
    code = DiscountCode.objects.filter(code=normalized).first()
    if code is None:
        raise DiscountCodeError(MSG_INVALID)
    _check_usable(code, school)
    return code


def reserve_redemption(
    code: DiscountCode,
    school,
    *,
    payment: Payment | None = None,
    batch_ref: str = "",
    amount: Decimal = Decimal("0.00"),
) -> DiscountRedemption:
    """يحجز استخداماً للكود — يُستدعى داخل معاملة إنشاء طلب الدفع.

    إعادة الفحص هنا ليست تكراراً: التحقق الأول جرى بلا قفل، وبين التحقق
    والحجز قد تسبقنا مدرسة أخرى إلى آخر استخدام.
    """
    locked = DiscountCode.objects.select_for_update().get(pk=code.pk)
    _check_usable(locked, school)
    try:
        # حفظٌ داخل نقطة استرجاع: خرقُ القيد الفريد في سباقٍ نادر يجب ألا
        # يُسمّم معاملة إنشاء الدفع كلها قبل أن نحوّله إلى رسالة مفهومة.
        with transaction.atomic():
            return DiscountRedemption.objects.create(
                code=locked,
                school=school,
                payment=payment,
                batch_ref=batch_ref or "",
                amount_discounted=amount,
            )
    except IntegrityError as exc:
        raise DiscountCodeError(MSG_ALREADY_USED) from exc


def release_dead_redemptions(*, batch_ref: str | None = None, payment_id: int | None = None) -> int:
    """يحرّر حجوزات الأكواد لطلبات ماتت قبل أن يتحقق أثرها.

    آمنة للتكرار: لا تحذف إلا حجزاً دفعتُه مرفوضة أو ملغاة **ولم يُطبَّق**
    أثرها — فدفعةٌ اعتُمدت وطُبّق أثرها ثم عُدّلت حالتها لا تُحرَّر تلقائياً.
    """
    qs = DiscountRedemption.objects.filter(
        payment__status__in=[Payment.Status.REJECTED, Payment.Status.CANCELLED],
        payment__effects_applied_at__isnull=True,
    )
    if batch_ref is not None:
        qs = qs.filter(payment__batch_ref=batch_ref)
    if payment_id is not None:
        qs = qs.filter(payment_id=payment_id)
    deleted, _details = qs.delete()
    return int(deleted)
