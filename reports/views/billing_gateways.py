# reports/views/billing_gateways.py
# -*- coding: utf-8 -*-
"""بوّابة الدفع (ميسر) والمصالحة الدورية للطلبات المعلّقة.

**القاعدة الحاكمة في هذه الوحدة:** التفعيل يعتمد على التحقق من الفاتورة **لدى
البوّابة**، لا على وصول العميل إلى صفحة النجاح ولا على محتوى الاستدعاء الراجع.
والاستدعاء نفسه قد يصل مرتين، فكل مسار هنا يجب أن يكون خاملاً (idempotent) —
وذلك ما يضمنه ``effects_applied_at`` في ``_apply_payment_effects``.
"""
# -*- coding: utf-8 -*-

from datetime import timedelta
from decimal import Decimal, ROUND_HALF_UP
from itertools import pairwise
import json
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlparse
import uuid

from django.core.exceptions import ImproperlyConfigured
from django.views.decorators.csrf import csrf_exempt

from core.observability import report_degraded as _degraded, soft_call, soft_fail

from ._helpers import *
from ._helpers import (
    _is_staff, _safe_next_url,
    _school_manager_label, _get_active_school,
    _clean_query_value, _clean_query_params, _parse_date_safe,
)
from ..mansour_knowledge import AUDIENCE_LABELS
from ..permissions import executive_director_schools_qs
from ..utils import create_system_notification
from ..flexible_pricing import (
    ANCHOR_CAPACITIES,
    PERIODS,
    build_flexible_pricing_catalog,
    normalize_teacher_capacity,
    period_key_for_days,
    quote_for_selection,
    serialize_flexible_pricing_catalog,
)
from ..pricing import SUBSCRIPTION_ADDON_NOTES, SUBSCRIPTION_INCLUDED_FEATURES
from ..moyasar_gateway import (
    MoyasarGatewayError,
    create_invoice as create_moyasar_invoice,
    fetch_invoice as fetch_moyasar_invoice,
    is_enabled as moyasar_is_enabled,
)

from .billing_core import *  # noqa: F401,F403
from .billing_core import (
    _cache_set,
    ARCHIVE_ADDON_ANNUAL_PRICE,
    ARCHIVE_ADDON_INCLUDED_STORAGE_GB,
    ARCHIVE_STORAGE_BLOCK_GB,
    ARCHIVE_STORAGE_BLOCK_PRICE,
    _archive_pricing,
    _ensure_default_archive_storage_option,
    _archive_storage_options,
    _renewal_plan_catalog,
    _payment_purpose_label,
    _record_subscription_payment_if_missing,
    _ApprovalError,
    _PURPOSE_APPLY_ORDER,
    _apply_payment_effects,
    _PaymentActor,
    _requested_school_id,
    _resolve_payment_actor,
    _subscription_redirect,
    _ACTING_SCHOOL_SESSION_KEY,
    _remember_acting_school,
    _subscription_return_redirect,
    _stamp_payer,
    _notify_managers_of_group_payment,
    _group_payer_badge,
    _PaymentSelectionError,
    _subscription_quote_from_request,
    _build_unified_payment_items,
    _create_unified_payment,
    _manager_payment_membership,
)


def _complete_moyasar_invoice(batch_ref: str, invoice: dict) -> None:
    invoice_id = str(invoice.get("id") or "").strip()
    invoice_status = str(invoice.get("status") or "").strip().lower()
    currency = str(invoice.get("currency") or "").strip().upper()
    metadata = invoice.get("metadata") if isinstance(invoice.get("metadata"), dict) else {}
    if invoice_status != "paid":
        raise _ApprovalError("فاتورة ميّسر لم تصل إلى حالة مدفوعة.")
    if currency != "SAR":
        raise _ApprovalError("عملة فاتورة ميّسر لا تطابق عملة الطلب.")
    if str(metadata.get("batch_ref") or "") != batch_ref:
        raise _ApprovalError("مرجع فاتورة ميّسر لا يطابق الطلب المحلي.")

    payment_attempts = invoice.get("payments") if isinstance(invoice.get("payments"), list) else []
    paid_attempt = next(
        (
            attempt
            for attempt in payment_attempts
            if isinstance(attempt, dict)
            and str(attempt.get("status") or "").lower() in {"paid", "captured"}
        ),
        {},
    )
    gateway_payment_id = str(paid_attempt.get("id") or "")[:160]

    pricing = _archive_pricing()
    today = timezone.localdate()
    with transaction.atomic():
        payments = list(
            Payment.objects.select_for_update()
            .filter(payment_method=Payment.Method.MOYASAR, batch_ref=batch_ref)
            .order_by("id")
        )
        if not payments or not invoice_id:
            raise _ApprovalError("طلب ميّسر غير معروف.")
        if any(payment.gateway_order_id != invoice_id for payment in payments):
            raise _ApprovalError("رقم فاتورة ميّسر لا يطابق الطلب المحلي.")

        expected_halalas = int(
            (
                sum((payment.amount for payment in payments), Decimal("0"))
                * Decimal("100")
            ).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        try:
            invoice_amount = int(invoice.get("amount"))
        except (TypeError, ValueError) as exc:
            raise _ApprovalError("مبلغ فاتورة ميّسر غير صالح.") from exc
        if invoice_amount != expected_halalas:
            raise _ApprovalError("مبلغ فاتورة ميّسر لا يطابق مبلغ الطلب.")

        payments.sort(key=lambda payment: _PURPOSE_APPLY_ORDER.get(payment.purpose, 99))
        for payment in payments:
            if payment.status == Payment.Status.APPROVED and payment.effects_applied_at:
                continue
            payment.status = Payment.Status.APPROVED
            payment.gateway_status = "paid"
            payment.gateway_capture_id = gateway_payment_id
            payment.gateway_completed_at = payment.gateway_completed_at or timezone.now()
            payment.save(
                update_fields=[
                    "status",
                    "gateway_status",
                    "gateway_capture_id",
                    "gateway_completed_at",
                    "updated_at",
                ]
            )
            _apply_payment_effects(payment, today, pricing)


def _sync_moyasar_batch(batch_ref: str) -> str:
    payment = (
        Payment.objects.filter(
            payment_method=Payment.Method.MOYASAR,
            batch_ref=batch_ref,
        )
        .order_by("id")
        .first()
    )
    if not payment or not payment.gateway_order_id:
        raise _ApprovalError("طلب ميّسر غير معروف.")
    invoice = fetch_moyasar_invoice(payment.gateway_order_id)
    invoice_status = str(invoice.get("status") or "").strip().lower()
    if invoice_status == "paid":
        _complete_moyasar_invoice(batch_ref, invoice)
    elif invoice_status in {"failed", "canceled", "expired", "voided"}:
        local_status = (
            Payment.Status.REJECTED
            if invoice_status == "failed"
            else Payment.Status.CANCELLED
        )
        Payment.objects.filter(
            payment_method=Payment.Method.MOYASAR,
            batch_ref=batch_ref,
            status=Payment.Status.PENDING,
        ).update(status=local_status, gateway_status=invoice_status)
    else:
        Payment.objects.filter(
            payment_method=Payment.Method.MOYASAR,
            batch_ref=batch_ref,
            status=Payment.Status.PENDING,
        ).update(gateway_status=invoice_status[:32])
    return invoice_status


@login_required(login_url="reports:login")
@ratelimit(key="user", rate="5/m", method="POST", block=True)
@require_http_methods(["POST"])
def moyasar_checkout_create(request):
    if not moyasar_is_enabled():
        messages.error(request, "الدفع الإلكتروني غير متاح حاليًا.")
        return _subscription_redirect(membership)

    membership = _manager_payment_membership(request)
    if not membership:
        messages.error(request, "هذه الخدمة مخصصة لإدارة المدرسة.")
        return redirect("reports:home")

    subscription = (
        SchoolSubscription.objects.filter(school=membership.school)
        .select_related("plan")
        .first()
    )
    try:
        items, warnings = _build_unified_payment_items(request, membership, subscription)
    except _PaymentSelectionError as exc:
        messages.error(request, str(exc))
        return _subscription_redirect(membership)

    _remember_acting_school(request, membership)
    batch_ref = uuid.uuid4().hex[:16]
    total = sum((Decimal(str(item["amount"])) for item in items), Decimal("0"))
    labels = "، ".join(item["label"] for item in items)
    callback_url = request.build_absolute_uri(
        reverse("reports:moyasar_callback", args=[batch_ref])
    )
    success_url = request.build_absolute_uri(
        reverse("reports:moyasar_return", args=[batch_ref])
    )
    back_url = request.build_absolute_uri(_subscription_redirect(membership).url)
    try:
        invoice = create_moyasar_invoice(
            amount=total,
            description=f"خدمات منصة توثيق: {labels}",
            callback_url=callback_url,
            success_url=success_url,
            back_url=back_url,
            metadata={
                "batch_ref": batch_ref,
                "school_id": str(membership.school_id),
            },
        )
    except (MoyasarGatewayError, ImproperlyConfigured):
        logger.exception("Moyasar invoice creation failed")
        messages.error(request, "تعذّر بدء الدفع الإلكتروني. حاول مجددًا أو استخدم طريقة أخرى.")
        return _subscription_redirect(membership)

    checkout_url = str(invoice.get("url") or "").strip()
    parsed_checkout_url = urlparse(checkout_url)
    checkout_host = (parsed_checkout_url.hostname or "").lower()
    if parsed_checkout_url.scheme != "https" or checkout_host != "checkout.moyasar.com":
        logger.error("Moyasar returned an unsafe checkout URL")
        messages.error(request, "تعذّر التحقق من رابط الدفع الإلكتروني.")
        return _subscription_redirect(membership)

    checkout_query = dict(parse_qsl(parsed_checkout_url.query, keep_blank_values=True))
    checkout_query["lang"] = "ar"
    checkout_url = parsed_checkout_url._replace(query=urlencode(checkout_query)).geturl()

    invoice_id = str(invoice.get("id") or "").strip()
    gateway_status = str(invoice.get("status") or "initiated")[:32]
    note = f"[فاتورة دفع إلكتروني {batch_ref.upper()}] {labels} — الإجمالي {total} ريال."
    with transaction.atomic():
        for item in items:
            Payment.objects.create(**_stamp_payer({
                "school": membership.school,
                "subscription": subscription,
                "requested_plan": item.get("requested_plan"),
                "requested_teacher_limit": item.get("requested_teacher_limit"),
                "purpose": item["purpose"],
                "amount": item["amount"],
                "archive_storage_gb": item.get("archive_storage_gb", 0),
                "notes": note,
                "batch_ref": batch_ref,
                "payment_method": Payment.Method.MOYASAR,
                "gateway_order_id": invoice_id,
                "gateway_checkout_id": invoice_id,
                "gateway_status": gateway_status,
                "created_by": request.user,
            }, membership))

    if warnings:
        messages.warning(request, "لم تُضف بعض العناصر: " + " ، ".join(warnings))
    _notify_managers_of_group_payment(membership, total=total, labels=labels)
    return redirect(checkout_url)


@require_http_methods(["GET"])
def moyasar_return(request, batch_ref: str):
    if not moyasar_is_enabled():
        messages.error(request, "الدفع الإلكتروني غير متاح حاليًا.")
        return _subscription_return_redirect(request)
    try:
        invoice_status = _sync_moyasar_batch(batch_ref)
    except (MoyasarGatewayError, ImproperlyConfigured, _ApprovalError):
        logger.exception("Moyasar return verification failed for batch %s", batch_ref)
        messages.error(request, "تعذّر التحقق من نتيجة الدفع الإلكتروني. سيُعاد التحقق تلقائيًا.")
    else:
        if invoice_status == "paid":
            messages.success(request, "تم تأكيد الدفع الإلكتروني وتفعيل الخدمات المختارة.")
        elif invoice_status in {"failed", "canceled", "expired", "voided"}:
            messages.error(request, "لم تكتمل عملية الدفع الإلكتروني. يمكنك إنشاء طلب جديد.")
        else:
            messages.info(request, "عملية الدفع الإلكتروني ما زالت بانتظار الإكمال.")
    return _subscription_return_redirect(request)


@login_required(login_url="reports:login")
@ratelimit(key="user", rate="10/m", method="POST", block=True)
@require_http_methods(["POST"])
def moyasar_checkout_cancel(request, payment_id: int):
    """Let a manager drop an electronic order they never paid.

    A customer who closes the Moyasar tab cannot get back to it — the checkout
    URL is single-use and is not kept — so without this the order sits pending
    forever and the school is stuck looking at a payment it cannot finish.

    Cancelling only touches our own records. If the customer somehow does pay
    the old link afterwards, the invoice becomes paid at Moyasar and the
    webhook activates the services anyway: _complete_moyasar_invoice keys off
    the invoice status, not off the local row being pending.
    """
    membership = _manager_payment_membership(request)
    payment = Payment.objects.filter(
        pk=payment_id,
        school=getattr(membership, "school", None),
        payment_method=Payment.Method.MOYASAR,
        status=Payment.Status.PENDING,
    ).first()
    if not membership or not payment or not payment.batch_ref:
        messages.error(request, "طلب الدفع الإلكتروني غير متاح للإلغاء.")
        return _subscription_redirect(membership)

    # Never cancel on our word alone — ask Moyasar first. A paid invoice whose
    # callback has not landed yet must be completed, not thrown away.
    try:
        invoice_status = _sync_moyasar_batch(payment.batch_ref)
    except (MoyasarGatewayError, ImproperlyConfigured, _ApprovalError):
        logger.exception("Moyasar cancel verification failed for batch %s", payment.batch_ref)
        messages.error(request, "تعذّر التحقق من حالة الطلب لدى مزود الدفع. حاول مجددًا.")
        return _subscription_redirect(membership)

    if invoice_status == "paid":
        messages.success(request, "الدفع مكتمل بالفعل، وتم تفعيل الخدمات المختارة.")
        return _subscription_redirect(membership)

    cancelled = Payment.objects.filter(
        payment_method=Payment.Method.MOYASAR,
        batch_ref=payment.batch_ref,
        status=Payment.Status.PENDING,
    ).update(status=Payment.Status.CANCELLED, gateway_status="customer_cancelled")
    if cancelled:
        messages.success(request, "أُلغي الطلب غير المدفوع. يمكنك إنشاء طلب جديد متى شئت.")
    else:
        messages.info(request, "لم يعد الطلب معلّقًا.")
    return _subscription_redirect(membership)


@csrf_exempt
# Unauthenticated by design — Moyasar calls it — and safe because the invoice is
# re-fetched from Moyasar rather than trusted from the request body. The limit
# only stops an anonymous client from replaying it to generate database lookups
# and outbound gateway calls.
@ratelimit(key="ip", rate="60/m", method="POST", block=True)
@require_http_methods(["POST"])
def moyasar_callback(request, batch_ref: str):
    if not moyasar_is_enabled():
        return JsonResponse({"detail": "Moyasar is disabled."}, status=404)
    try:
        invoice_status = _sync_moyasar_batch(batch_ref)
    except (MoyasarGatewayError, ImproperlyConfigured, _ApprovalError):
        logger.exception("Moyasar callback verification failed for batch %s", batch_ref)
        return JsonResponse({"detail": "Could not verify invoice."}, status=502)
    return JsonResponse({"ok": True, "status": invoice_status})


def _abandon_stale_gateway_batch(payment, *, cutoff) -> bool:
    """Cancel an electronic order the customer walked away from.

    The gateway has just told us the invoice is still unpaid. A hosted checkout
    URL is single-use and is not stored, so once the customer closes that tab
    there is no way back to it — the order can never be completed and leaving it
    pending only shows the school a payment it cannot finish, while blocking the
    add-on purchases that refuse to queue behind a pending request.

    Only the local rows are touched. Should the customer somehow still pay,
    the invoice turns paid at the gateway and the webhook activates the
    services regardless of what the local row says.
    """
    if payment.created_at > cutoff:
        return False

    updated = Payment.objects.filter(
        payment_method=payment.payment_method,
        batch_ref=payment.batch_ref,
        status=Payment.Status.PENDING,
    ).update(status=Payment.Status.CANCELLED, gateway_status="abandoned")
    if updated:
        logger.info(
            "Cancelled abandoned %s order batch=%s rows=%s",
            payment.payment_method,
            payment.batch_ref,
            updated,
        )
    return bool(updated)


def reconcile_pending_gateway_payments(
    *,
    max_age_days: int = 7,
    limit: int = 200,
    abandon_after_minutes: int | None = None,
) -> dict:
    """Re-check gateway payments still sitting as PENDING and finish them.

    Activation depended entirely on the customer's browser returning to
    ``moyasar_return`` or on the gateway's callback reaching us. Both can fail —
    a closed tab, a dropped webhook, a deploy restarting the container mid-call —
    and nothing retried. The school had paid, the money was captured, and the
    subscription silently never activated until someone complained.

    This walks recent pending gateway payments and re-runs the same verified
    completion path the callback uses: the amount is still re-checked against the
    gateway and effects are still applied once (``effects_applied_at``), so
    reconciling is safe to repeat.

    Payments older than ``max_age_days`` are left alone for manual review rather
    than retried forever.
    """
    summary = {
        "checked": 0,
        "activated": 0,
        "still_pending": 0,
        "abandoned": 0,
        "failed": 0,
        # Payment rows that had to be rescued, so the caller can raise an alert
        # per rescue rather than per sweep.
        "recovered_payment_ids": [],
    }
    cutoff = timezone.now() - timedelta(days=max(1, int(max_age_days)))

    if abandon_after_minutes is None:
        abandon_after_minutes = int(
            getattr(settings, "PAYMENT_ABANDON_AFTER_MINUTES", 60) or 0
        )
    # Zero disables abandonment, leaving the sweep purely a rescue pass.
    abandon_cutoff = (
        timezone.now() - timedelta(minutes=abandon_after_minutes)
        if abandon_after_minutes > 0
        else None
    )

    pending = (
        Payment.objects.filter(
            status=Payment.Status.PENDING,
            payment_method=Payment.Method.MOYASAR,
            created_at__gte=cutoff,
        )
        .exclude(gateway_order_id="")
        .order_by("created_at")
    )

    # One attempt per gateway order, not per payment row in the batch.
    seen: set[tuple[str, str]] = set()
    for payment in pending[: max(1, int(limit))]:
        key = (payment.payment_method, payment.batch_ref or payment.gateway_order_id)
        if key in seen:
            continue
        seen.add(key)
        summary["checked"] += 1

        try:
            if not payment.batch_ref:
                continue
            # A disabled gateway may still have historical pending rows.
            # Without credentials those rows cannot be reconciled, and
            # retrying every row every 20 minutes only floods production
            # logs with the same configuration exception. Keep the rows
            # pending for an operator to resolve once credentials exist.
            if not str(getattr(settings, "MOYASAR_SECRET_KEY", "") or "").strip():
                summary["still_pending"] += 1
                continue
            status = _sync_moyasar_batch(payment.batch_ref)
            if status == "paid":
                summary["activated"] += 1
                summary["recovered_payment_ids"].append(payment.pk)
            elif abandon_cutoff is not None and _abandon_stale_gateway_batch(
                payment, cutoff=abandon_cutoff
            ):
                summary["abandoned"] += 1
            else:
                summary["still_pending"] += 1
        except Exception:
            summary["failed"] += 1
            logger.exception(
                "Gateway reconciliation failed method=%s order=%s batch=%s",
                payment.payment_method,
                payment.gateway_order_id,
                payment.batch_ref,
            )

    return summary

