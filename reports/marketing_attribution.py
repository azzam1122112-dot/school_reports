from __future__ import annotations

from urllib.parse import urlparse

from django.http import HttpRequest


SESSION_KEY = "_marketing_attribution"

_TRACKING_FIELDS = {
    "marketing_source": ("utm_source", 120),
    "marketing_medium": ("utm_medium", 120),
    "marketing_campaign": ("utm_campaign", 200),
    "marketing_content": ("utm_content", 200),
    "marketing_term": ("utm_term", 200),
}


def _clean(value: object, max_length: int) -> str:
    text = "".join(
        character
        for character in str(value or "").strip()
        if character.isprintable()
    )
    return text[:max_length]


def _referrer_host(request: HttpRequest) -> str:
    raw_referrer = _clean(request.META.get("HTTP_REFERER"), 1000)
    if not raw_referrer:
        return ""
    try:
        host = (urlparse(raw_referrer).hostname or "").lower()
    except ValueError:
        return ""
    request_host = (request.get_host().split(":", 1)[0] or "").lower()
    if not host or host == request_host:
        return ""
    return host[:255]


def capture_marketing_attribution(request: HttpRequest) -> None:
    """Keep privacy-friendly first-touch attribution in the existing session.

    Only campaign parameters, an advertising click identifier, and the
    referring hostname are retained. Full external URLs, page contents, IP
    addresses, and third-party tracking cookies are not stored.
    """

    current = dict(request.session.get(SESSION_KEY) or {})
    incoming = {
        field_name: _clean(request.GET.get(query_name), max_length)
        for field_name, (query_name, max_length) in _TRACKING_FIELDS.items()
    }
    has_campaign = any(incoming.values())

    click_id = ""
    if request.GET.get("gclid"):
        click_id = f"gclid:{_clean(request.GET.get('gclid'), 249)}"
    elif request.GET.get("fbclid"):
        click_id = f"fbclid:{_clean(request.GET.get('fbclid'), 249)}"

    referrer = _referrer_host(request)

    if has_campaign:
        for field_name, value in incoming.items():
            if value:
                current.setdefault(field_name, value)
    elif not current.get("marketing_source"):
        current["marketing_source"] = "referral" if referrer else "direct"
        current["marketing_medium"] = "referral" if referrer else "none"

    if click_id:
        current.setdefault("marketing_click_id", click_id[:255])
    if referrer:
        current.setdefault("marketing_referrer", referrer)

    request.session[SESSION_KEY] = current


def school_marketing_fields(request: HttpRequest) -> dict[str, str]:
    current = dict(request.session.get(SESSION_KEY) or {})
    return {
        field_name: _clean(current.get(field_name), max_length)
        for field_name, max_length in (
            ("marketing_source", 120),
            ("marketing_medium", 120),
            ("marketing_campaign", 200),
            ("marketing_content", 200),
            ("marketing_term", 200),
            ("marketing_click_id", 255),
            ("marketing_referrer", 255),
        )
    }
