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

    The session is written only when there is genuinely new attribution to
    remember. A plain direct visit to a public page therefore leaves the
    session untouched, so anonymous marketing traffic does not create one
    session row (and one ``Set-Cookie``) per visitor. The "direct"/"referral"
    defaults are applied at read time instead — see
    :func:`school_marketing_fields`.
    """

    current = dict(request.session.get(SESSION_KEY) or {})
    updated = dict(current)

    for field_name, (query_name, max_length) in _TRACKING_FIELDS.items():
        value = _clean(request.GET.get(query_name), max_length)
        if value:
            updated.setdefault(field_name, value)

    click_id = ""
    if request.GET.get("gclid"):
        click_id = f"gclid:{_clean(request.GET.get('gclid'), 249)}"
    elif request.GET.get("fbclid"):
        click_id = f"fbclid:{_clean(request.GET.get('fbclid'), 249)}"
    if click_id:
        updated.setdefault("marketing_click_id", click_id[:255])

    referrer = _referrer_host(request)
    if referrer:
        updated.setdefault("marketing_referrer", referrer)

    if updated == current:
        return

    request.session[SESSION_KEY] = updated


def school_marketing_fields(request: HttpRequest) -> dict[str, str]:
    current = dict(request.session.get(SESSION_KEY) or {})
    values = {
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

    # Visitors who arrived without any campaign parameter are labelled at read
    # time, so the capture step never has to write a session just to record
    # "direct".
    has_campaign = any(values[field_name] for field_name in _TRACKING_FIELDS)
    if not has_campaign and not values["marketing_source"]:
        came_from_referrer = bool(values["marketing_referrer"])
        values["marketing_source"] = "referral" if came_from_referrer else "direct"
        values["marketing_medium"] = "referral" if came_from_referrer else "none"

    return values
