from __future__ import annotations

import base64
import logging
import re
from email.utils import parseaddr

from django.conf import settings
from django.core.mail.backends.base import BaseEmailBackend

from .resend_email import ResendError, _api_request

logger = logging.getLogger(__name__)


def _resend_tag_value(value: str) -> str:
    """Return a Resend-compatible tag value.

    Resend tags are useful in the dashboard, but their values are deliberately
    restrictive: ASCII letters, numbers, underscores and dashes only. Domains
    contain dots, so keep the meaning while making the value acceptable.
    """
    normalized = re.sub(r"[^A-Za-z0-9_-]+", "-", str(value or "").strip())
    return normalized.strip("-")[:256]


class ResendEmailBackend(BaseEmailBackend):
    """Django email backend that sends system mail through Resend."""

    def send_messages(self, email_messages) -> int:
        if not email_messages:
            return 0

        sent_count = 0
        for message in email_messages:
            try:
                self._send(message)
            except Exception:
                if not self.fail_silently:
                    raise
                logger.exception("Resend email backend failed silently")
            else:
                sent_count += 1
        return sent_count

    def _send(self, message) -> None:
        from_email = message.from_email or getattr(settings, "DEFAULT_FROM_EMAIL", "")
        payload = {
            "from": from_email,
            "to": list(message.to or []),
            "subject": message.subject or "",
        }
        if message.cc:
            payload["cc"] = list(message.cc)
        if message.bcc:
            payload["bcc"] = list(message.bcc)
        if getattr(message, "reply_to", None):
            payload["reply_to"] = list(message.reply_to)

        text_body = message.body or ""
        html_body = ""
        if getattr(message, "content_subtype", "plain") == "html":
            html_body = text_body
            text_body = ""
        for content, mimetype in getattr(message, "alternatives", []) or []:
            if str(mimetype).lower() == "text/html":
                html_body = content

        if text_body:
            payload["text"] = text_body
        if html_body:
            payload["html"] = html_body
        if not text_body and not html_body:
            payload["text"] = ""

        attachments = []
        for attachment in getattr(message, "attachments", []) or []:
            filename, content, _mimetype = attachment
            if isinstance(content, str):
                content = content.encode("utf-8")
            attachments.append(
                {
                    "filename": filename,
                    "content": base64.b64encode(content).decode("ascii"),
                }
            )
        if attachments:
            payload["attachments"] = attachments

        tags = [
            {"name": "source", "value": "django_system_email"},
        ]
        _, sender_address = parseaddr(str(from_email or ""))
        if sender_address:
            sender_domain = _resend_tag_value(sender_address.split("@", 1)[-1])
            if sender_domain:
                tags.append({"name": "sender_domain", "value": sender_domain})
        payload["tags"] = tags

        response = _api_request("/emails", method="POST", payload=payload)
        if not str(response.get("id") or "").strip():
            raise ResendError("Resend did not return an email id.")
