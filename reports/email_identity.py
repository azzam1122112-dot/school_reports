from __future__ import annotations

from email.utils import parseaddr

from django.conf import settings


def system_sender_name() -> str:
    return (
        str(getattr(settings, "DEFAULT_FROM_NAME", "") or "").strip()
        or str(getattr(settings, "PLATFORM_EMAIL_SENDER_NAME", "") or "").strip()
        or "منصة توثيق"
    )


def format_system_from_email(from_email: str | None = None) -> str:
    """Attach the platform display name when a system sender is only an address."""

    value = str(from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "") or "").strip()
    display_name, address = parseaddr(value)
    if not address:
        return value
    if display_name:
        return value
    return f"{system_sender_name()} <{address}>"
