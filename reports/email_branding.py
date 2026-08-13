from __future__ import annotations

from django.conf import settings
from django.templatetags.static import static
from django.template.loader import render_to_string


PLATFORM_NAME = "منصة توثيق"


def platform_url(path: str = "/") -> str:
    base = (getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    normalized = f"/{path.lstrip('/')}"
    return f"{base}{normalized}" if base else normalized


def email_brand_context(**overrides) -> dict:
    base = (getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    logo_path = static("img/logo1.png")
    context = {
        "platform_name": PLATFORM_NAME,
        "platform_url": platform_url(),
        "logo_url": f"{base}{logo_path}" if base else "",
        "support_email": (
            getattr(settings, "SECURITY_CONTACT_EMAIL", "")
            or "support@tawtheeq-ksa.com"
        ).strip(),
        "email_title": "رسالة من منصة توثيق",
        "email_eyebrow": "مركز الاتصال الرسمي",
        "email_preheader": "رسالة رسمية من منصة توثيق.",
        "email_tone": "default",
        "recipient_name": "",
        "body_html": "",
        "action_url": "",
        "action_label": "",
        "notice_title": "",
        "notice_text": "",
        "meta_items": [],
        "is_automated": True,
    }
    context.update(overrides)
    return context


def render_branded_email(template_name: str, **context) -> str:
    return render_to_string(
        f"reports/emails/{template_name}",
        email_brand_context(**context),
    )
