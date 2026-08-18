from __future__ import annotations

from collections import defaultdict
from decimal import Decimal

from django.conf import settings
from django.db.models import Q
from django.templatetags.static import static
from django.utils import timezone

from .models import Payment


class InvoiceUnavailable(Exception):
    """The payment is not eligible for a final customer invoice yet."""


def _business_identity() -> dict:
    return {
        "legal_name": str(
            getattr(settings, "BUSINESS_LEGAL_NAME", "") or "منصة توثيق"
        ).strip(),
        "commercial_registration": str(
            getattr(settings, "BUSINESS_COMMERCIAL_REGISTRATION", "") or ""
        ).strip(),
        "freelance_document_number": str(
            getattr(settings, "BUSINESS_FREELANCE_DOCUMENT_NUMBER", "") or ""
        ).strip(),
        "tax_number": str(
            getattr(settings, "BUSINESS_TAX_NUMBER", "") or ""
        ).strip(),
        "address": str(getattr(settings, "BUSINESS_ADDRESS", "") or "").strip(),
        "support_email": str(
            getattr(settings, "BUSINESS_SUPPORT_EMAIL", "") or ""
        ).strip(),
        "support_phone": str(
            getattr(settings, "BUSINESS_SUPPORT_PHONE", "") or ""
        ).strip(),
    }


def _group_payments(payment: Payment) -> list[Payment]:
    queryset = Payment.objects.filter(school_id=payment.school_id).select_related(
        "school", "requested_plan", "payer_group", "discount_code"
    )
    if payment.batch_ref:
        queryset = queryset.filter(batch_ref=payment.batch_ref)
    else:
        queryset = queryset.filter(pk=payment.pk)
    return list(queryset.order_by("id"))


def _invoice_number(payment: Payment, payments: list[Payment]) -> str:
    anchor = min(payments, key=lambda row: row.pk)
    year = timezone.localtime(anchor.created_at).year
    token = payment.batch_ref.upper() if payment.batch_ref else f"{anchor.pk:08d}"
    return f"TQ-{year}-{token}"


def _item_description(payment: Payment) -> str:
    if payment.purpose == Payment.Purpose.ARCHIVE_ADDON:
        return "تفعيل أو تجديد خدمة الأرشفة السنوية"
    if payment.purpose == Payment.Purpose.WORK_STORAGE:
        return f"زيادة مساحة عمل المدرسة بمقدار {payment.archive_storage_gb}GB"
    if payment.purpose == Payment.Purpose.ARCHIVE_SPACE:
        return f"زيادة مساحة الأرشفة السنوية بمقدار {payment.archive_storage_gb}GB"

    plan_name = getattr(payment.requested_plan, "name", "") or "اشتراك المدرسة"
    details = []
    if payment.requested_teacher_limit:
        details.append(f"سعة {payment.requested_teacher_limit} معلماً")
    duration = int(getattr(payment.requested_plan, "days_duration", 0) or 0)
    if duration:
        details.append(f"مدة {duration} يوماً")
    return " - ".join([plan_name, *details])


def _invoice_row_eligible(row: Payment) -> bool:
    # بندٌ صفريّ المبلغ يبقى مفوتراً إن كان صفره أثرَ كود خصم غطّاه كاملاً.
    return row.status == Payment.Status.APPROVED and (
        row.amount > 0 or (row.discount_amount or 0) > 0
    )


def build_invoice_context(payment: Payment) -> dict:
    payments = _group_payments(payment)
    if not payments or any(not _invoice_row_eligible(row) for row in payments):
        raise InvoiceUnavailable("لا تتوفر الفاتورة إلا بعد اعتماد جميع بنود الطلب.")

    issued_candidates = [
        row.gateway_completed_at or row.effects_applied_at or row.updated_at
        for row in payments
    ]
    issued_at = timezone.localtime(max(issued_candidates))
    # المجموع قبل الخصم، ثم سطر الخصم، ثم الضريبة (صفر حالياً)، ثم المدفوع فعلاً.
    subtotal = sum((row.amount + (row.discount_amount or 0) for row in payments), Decimal("0.00"))
    discount_total = sum(((row.discount_amount or 0) for row in payments), Decimal("0.00"))
    discount_codes_label = "، ".join(
        sorted({row.discount_code.code for row in payments if row.discount_code_id and row.discount_code})
    )
    tax_amount = Decimal("0.00")
    total = subtotal - discount_total + tax_amount
    anchor = min(payments, key=lambda row: row.pk)
    gateway_reference = next(
        (
            row.gateway_capture_id or row.gateway_order_id
            for row in payments
            if row.gateway_capture_id or row.gateway_order_id
        ),
        "",
    )
    payment_reference = gateway_reference or payment.batch_ref or f"PAY-{anchor.pk:08d}"
    school = anchor.school
    payment_method = (
        "دفع إلكتروني" if anchor.payment_method == Payment.Method.MOYASAR
        else anchor.get_payment_method_display()
    )
    payer_name = "إدارة المدرسة"
    if anchor.payer_kind == Payment.PayerKind.GROUP_DIRECTOR:
        payer_name = getattr(anchor.payer_group, "name", "") or "مجموعة المدارس"
    elif anchor.payer_kind == Payment.PayerKind.PLATFORM:
        payer_name = "إدارة المنصة"

    return {
        "invoice_number": _invoice_number(anchor, payments),
        "issued_at": issued_at,
        "paid_at": issued_at,
        "status_label": "مدفوعة",
        "currency": "SAR",
        "business": _business_identity(),
        "customer": {
            "name": school.name,
            "code": school.code,
            "city": school.city or "",
            "phone": school.phone or "",
            "email": school.email or "",
        },
        "items": [
            {
                # أعمدة الجدول قبل الخصم حتى يساوي مجموعها «المجموع قبل الضريبة»،
                # ثم يظهر الخصم سطراً مستقلاً في الملخص.
                "description": _item_description(row),
                "quantity": 1,
                "unit_price": row.amount + (row.discount_amount or 0),
                "amount": row.amount + (row.discount_amount or 0),
            }
            for row in payments
        ],
        "subtotal": subtotal,
        "discount_total": discount_total,
        "discount_codes_label": discount_codes_label,
        "tax_amount": tax_amount,
        "total": total,
        "payment_method": payment_method,
        "payment_reference": payment_reference,
        "payer_name": payer_name,
        "anchor_payment_id": anchor.pk,
        "payment_ids": [row.pk for row in payments],
        "logo_url": static("img/logo1.png"),
    }


def decorate_payments_with_invoice_access(payments) -> None:
    """Attach invoice availability and canonical payment id without N+1 queries."""
    rows = list(payments)
    grouped_keys = {
        (row.school_id, row.batch_ref)
        for row in rows
        if row.batch_ref and _invoice_row_eligible(row)
    }
    related_by_key: dict[tuple[int, str], list[Payment]] = defaultdict(list)
    if grouped_keys:
        query = Q()
        for school_id, batch_ref in grouped_keys:
            query |= Q(school_id=school_id, batch_ref=batch_ref)
        for related in Payment.objects.filter(query).only(
            "id", "school_id", "batch_ref", "status", "amount", "discount_amount"
        ):
            related_by_key[(related.school_id, related.batch_ref)].append(related)

    rendered_invoice_keys = set()
    for row in rows:
        row.invoice_available = False
        row.invoice_anchor_id = row.pk
        if not _invoice_row_eligible(row):
            continue
        if not row.batch_ref:
            invoice_key = (row.school_id, row.pk)
            row.invoice_available = invoice_key not in rendered_invoice_keys
            rendered_invoice_keys.add(invoice_key)
            continue
        related = related_by_key.get((row.school_id, row.batch_ref), [])
        invoice_key = (row.school_id, row.batch_ref)
        group_is_ready = bool(related) and all(
            _invoice_row_eligible(item) for item in related
        )
        if related:
            row.invoice_anchor_id = min(item.pk for item in related)
        row.invoice_available = group_is_ready and invoice_key not in rendered_invoice_keys
        if group_is_ready:
            rendered_invoice_keys.add(invoice_key)
