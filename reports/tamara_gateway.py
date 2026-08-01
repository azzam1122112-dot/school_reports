from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone


class TamaraGatewayError(Exception):
    pass


def is_enabled() -> bool:
    return bool(getattr(settings, "TAMARA_ENABLED", False))


def _api_token() -> str:
    token = str(getattr(settings, "TAMARA_API_TOKEN", "") or "").strip()
    if not token:
        raise ImproperlyConfigured("TAMARA_API_TOKEN is required when Tamara payments are enabled.")
    return token


def _amount(value: Decimal | int | str) -> dict[str, Any]:
    return {"amount": float(Decimal(str(value))), "currency": "SAR"}


def _request(
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    method: str = "POST",
    timeout: float | None = None,
) -> dict[str, Any]:
    base_url = str(getattr(settings, "TAMARA_API_BASE_URL", "") or "").rstrip("/")
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(
        f"{base_url}{path}",
        data=body,
        method=method,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {_api_token()}",
            "Content-Type": "application/json",
        },
    )
    request_timeout = timeout if timeout is not None else int(getattr(settings, "TAMARA_REQUEST_TIMEOUT", 15) or 15)
    try:
        with urlopen(request, timeout=request_timeout) as response:
            raw = response.read()
    except HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise TamaraGatewayError(f"Tamara API returned HTTP {exc.code}: {detail}") from exc
    except (URLError, TimeoutError, OSError) as exc:
        raise TamaraGatewayError("Could not connect to Tamara API.") from exc

    try:
        return json.loads(raw.decode("utf-8")) if raw else {}
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TamaraGatewayError("Tamara API returned an invalid JSON response.") from exc


def create_checkout(payload: dict[str, Any]) -> dict[str, Any]:
    response = _request("/checkout", payload=payload)
    if not response.get("order_id") or not response.get("checkout_url"):
        raise TamaraGatewayError("Tamara checkout response is missing order details.")
    return response


def get_order(order_id: str) -> dict[str, Any]:
    return _request(f"/orders/{order_id}", method="GET")


def is_customer_eligible(*, amount: Decimal, phone: str, email: str) -> bool:
    digits = "".join(character for character in str(phone or "") if character.isdigit())
    if digits.startswith("0"):
        digits = f"966{digits[1:]}"
    elif not digits.startswith("966"):
        digits = f"966{digits}"
    try:
        response = _request(
            "/pre-checkout/v1/eligibility",
            payload={
                "order": {"amount": float(amount), "currency": "SAR"},
                "customer": {"phone_number": digits, "email": email.strip()},
            },
            timeout=0.2,
        )
    except TamaraGatewayError:
        return True
    return response.get("is_eligible") is not False


def build_checkout_payload(
    *,
    order_reference: str,
    items: list[dict[str, Any]],
    customer_name: str,
    customer_phone: str,
    customer_email: str,
    city: str,
    address: str,
    success_url: str,
    failure_url: str,
    cancel_url: str,
    risk_assessment: dict[str, Any],
    is_mobile: bool,
) -> dict[str, Any]:
    name_parts = [part for part in (customer_name or "").split() if part]
    first_name = name_parts[0] if name_parts else "مدير"
    last_name = " ".join(name_parts[1:]) if len(name_parts) > 1 else "المدرسة"
    digits = "".join(character for character in str(customer_phone or "") if character.isdigit())
    if digits.startswith("966"):
        digits = digits[3:]
    if digits.startswith("0"):
        digits = digits[1:]
    if not digits or not customer_email.strip() or not city.strip() or not address.strip():
        raise TamaraGatewayError("Customer phone, email, city, and address are required for Tamara checkout.")

    tamara_items = []
    total = Decimal("0")
    for index, item in enumerate(items, start=1):
        item_amount = Decimal(str(item["amount"]))
        total += item_amount
        tamara_items.append(
            {
                "reference_id": f"{order_reference}-{index}",
                "type": "Subscription - Digital",
                "name": str(item["label"])[:255],
                "sku": f"TAWTHEEQ-{item['purpose']}-{index}"[:128],
                "quantity": 1,
                "unit_price": _amount(item_amount),
                "total_amount": _amount(item_amount),
                "tax_amount": _amount(0),
                "discount_amount": _amount(0),
            }
        )

    consumer = {
        "first_name": first_name[:50],
        "last_name": last_name[:50],
        "phone_number": digits,
        "email": customer_email.strip()[:128],
    }

    shipping_address = {
        "first_name": first_name[:50],
        "last_name": last_name[:50],
        "line1": address.strip()[:255],
        "city": city.strip()[:120],
        "country_code": "SA",
        "phone_number": digits,
    }
    return {
        "order_reference_id": order_reference,
        "order_number": order_reference,
        "total_amount": _amount(total),
        "shipping_amount": _amount(0),
        "tax_amount": _amount(0),
        "description": "اشتراك وخدمات منصة توثيق",
        "country_code": "SA",
        "payment_type": "PAY_BY_INSTALMENTS",
        "instalments": int(getattr(settings, "TAMARA_INSTALMENTS", 4) or 4),
        "items": tamara_items,
        "consumer": consumer,
        "shipping_address": shipping_address,
        "billing_address": shipping_address,
        "risk_assessment": risk_assessment,
        "merchant_url": {
            "success": success_url,
            "failure": failure_url,
            "cancel": cancel_url,
        },
        "platform": "Tawtheeq Web",
        "is_mobile": bool(is_mobile),
        "locale": "ar_SA",
        "additional_data": {"delivery_method": "Digital delivery"},
    }


def authorise_order(order_id: str) -> dict[str, Any]:
    return _request(f"/orders/{order_id}/authorise", payload={})


def capture_order(order_id: str, amount: Decimal) -> dict[str, Any]:
    return _request(
        "/payments/capture",
        payload={
            "order_id": order_id,
            "total_amount": _amount(amount),
            "shipping_amount": _amount(0),
            "tax_amount": _amount(0),
            "discount_amount": _amount(0),
            "shipping_info": {
                "shipped_at": timezone.now().isoformat().replace("+00:00", "Z"),
                "shipping_company": "Tawtheeq Digital Delivery",
            },
        },
    )


def verify_notification_token(token: str) -> dict[str, Any]:
    secret = str(getattr(settings, "TAMARA_NOTIFICATION_TOKEN", "") or "").strip()
    if not secret:
        raise ImproperlyConfigured("TAMARA_NOTIFICATION_TOKEN is required for Tamara webhooks.")

    parts = (token or "").split(".")
    if len(parts) != 3:
        raise TamaraGatewayError("Invalid Tamara notification token.")

    signing_input = f"{parts[0]}.{parts[1]}".encode("ascii")
    expected = hmac.new(secret.encode("utf-8"), signing_input, hashlib.sha256).digest()
    padding = "=" * (-len(parts[2]) % 4)
    try:
        supplied = base64.urlsafe_b64decode(parts[2] + padding)
    except (ValueError, TypeError) as exc:
        raise TamaraGatewayError("Invalid Tamara notification signature.") from exc
    if not hmac.compare_digest(expected, supplied):
        raise TamaraGatewayError("Invalid Tamara notification signature.")

    payload_padding = "=" * (-len(parts[1]) % 4)
    try:
        payload = json.loads(base64.urlsafe_b64decode(parts[1] + payload_padding).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise TamaraGatewayError("Invalid Tamara notification payload.") from exc

    expires_at = payload.get("exp")
    if expires_at is not None and int(expires_at) < int(time.time()):
        raise TamaraGatewayError("Expired Tamara notification token.")
    if payload.get("iss") not in {None, "Tamara"}:
        raise TamaraGatewayError("Invalid Tamara notification issuer.")
    return payload