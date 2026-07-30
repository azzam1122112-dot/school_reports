from __future__ import annotations

import hashlib
import hmac
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any

import requests
from django.conf import settings


class TapPaymentError(RuntimeError):
    """A safe, user-displayable Tap integration error."""


class TapPaymentValidationError(TapPaymentError):
    """Tap returned a response that does not match the local order."""


_CURRENCY_DECIMALS = {
    "BHD": 3,
    "JOD": 3,
    "KWD": 3,
    "OMR": 3,
}


def tap_is_configured() -> bool:
    return bool(
        getattr(settings, "TAP_ENABLED", False)
        and str(getattr(settings, "TAP_SECRET_KEY", "") or "").strip()
        and str(getattr(settings, "TAP_MERCHANT_ID", "") or "").strip()
    )


def _secret_key() -> str:
    secret = str(getattr(settings, "TAP_SECRET_KEY", "") or "").strip()
    if not tap_is_configured() or not secret:
        raise TapPaymentError("بوابة Tap غير مهيأة بعد. تواصل مع إدارة المنصة.")
    return secret


def _api_url(path: str) -> str:
    base = str(
        getattr(settings, "TAP_API_BASE_URL", "https://api.tap.company/v2")
        or "https://api.tap.company/v2"
    ).rstrip("/")
    return f"{base}/{path.lstrip('/')}"


def _request(method: str, path: str, *, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    headers = {
        "Authorization": f"Bearer {_secret_key()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "lang_code": "ar",
    }
    timeout = (
        float(getattr(settings, "TAP_CONNECT_TIMEOUT_SECONDS", 5)),
        float(getattr(settings, "TAP_READ_TIMEOUT_SECONDS", 20)),
    )
    try:
        response = requests.request(
            method,
            _api_url(path),
            headers=headers,
            json=payload,
            timeout=timeout,
        )
    except requests.RequestException as exc:
        raise TapPaymentError("تعذّر الاتصال ببوابة الدفع. حاول مرة أخرى بعد قليل.") from exc

    try:
        data = response.json()
    except ValueError as exc:
        raise TapPaymentError("وصل رد غير صالح من بوابة الدفع.") from exc

    if not isinstance(data, dict):
        raise TapPaymentError("وصل رد غير صالح من بوابة الدفع.")

    if response.status_code >= 400:
        errors = data.get("errors")
        message = ""
        if isinstance(errors, list) and errors and isinstance(errors[0], dict):
            message = str(errors[0].get("description") or errors[0].get("message") or "").strip()
        message = message or str(data.get("message") or "").strip()
        if message:
            raise TapPaymentError(f"رفضت بوابة الدفع الطلب: {message[:180]}")
        raise TapPaymentError("رفضت بوابة الدفع الطلب. تحقق من إعدادات الحساب وحاول مجددًا.")

    return data


def create_charge(payload: dict[str, Any]) -> dict[str, Any]:
    return _request("POST", "charges/", payload=payload)


def retrieve_charge(charge_id: str) -> dict[str, Any]:
    charge_id = str(charge_id or "").strip()
    if not re.fullmatch(r"chg_[A-Za-z0-9_-]{8,120}", charge_id):
        raise TapPaymentValidationError("رقم عملية Tap غير صالح.")
    return _request("GET", f"charges/{charge_id}")


def currency_amount(value: Any, currency: str) -> str:
    currency = str(currency or "").upper()
    decimals = _CURRENCY_DECIMALS.get(currency, 2)
    quantum = Decimal("1").scaleb(-decimals)
    try:
        amount = Decimal(str(value)).quantize(quantum, rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise TapPaymentValidationError("قيمة عملية Tap غير صالحة.") from exc
    return f"{amount:.{decimals}f}"


def webhook_hashstring(payload: dict[str, Any], secret_key: str | None = None) -> str:
    reference = payload.get("reference") if isinstance(payload.get("reference"), dict) else {}
    transaction = payload.get("transaction") if isinstance(payload.get("transaction"), dict) else {}
    currency = str(payload.get("currency") or "").upper()
    to_hash = (
        f"x_id{payload.get('id') or ''}"
        f"x_amount{currency_amount(payload.get('amount'), currency)}"
        f"x_currency{currency}"
        f"x_gateway_reference{reference.get('gateway') or ''}"
        f"x_payment_reference{reference.get('payment') or ''}"
        f"x_status{payload.get('status') or ''}"
        f"x_created{transaction.get('created') or ''}"
    )
    key = (secret_key or _secret_key()).encode("utf-8")
    return hmac.new(key, to_hash.encode("utf-8"), hashlib.sha256).hexdigest()


def verify_webhook_hash(payload: dict[str, Any], posted_hashstring: str) -> bool:
    posted = str(posted_hashstring or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", posted):
        return False
    expected = webhook_hashstring(payload)
    return hmac.compare_digest(expected, posted)


def normalize_saudi_phone(value: str) -> dict[str, str] | None:
    digits = re.sub(r"\D", "", str(value or ""))
    if digits.startswith("00966"):
        digits = digits[5:]
    elif digits.startswith("966"):
        digits = digits[3:]
    elif digits.startswith("0"):
        digits = digits[1:]
    if re.fullmatch(r"5\d{8}", digits):
        return {"country_code": "966", "number": digits}
    return None


def customer_payload(user: Any) -> dict[str, Any]:
    name = " ".join(str(getattr(user, "name", "") or "").split()) or "عميل"
    parts = name.split(" ", 1)
    customer: dict[str, Any] = {
        "first_name": parts[0][:40],
        "last_name": (parts[1] if len(parts) > 1 else parts[0])[:40],
    }
    email = str(getattr(user, "email", "") or "").strip()
    if email:
        customer["email"] = email[:254]
    phone = normalize_saudi_phone(str(getattr(user, "phone", "") or ""))
    if phone:
        customer["phone"] = phone
    return customer
