from __future__ import annotations

import json
from urllib.error import HTTPError


AI_SERVICE_PAUSED_MESSAGE = (
    "خدمة الذكاء الاصطناعي متوقفة مؤقتًا حاليًا. "
    "يمكنك متابعة استخدام بقية خدمات المنصة كالمعتاد."
)

OPENAI_SPEND_LIMIT_ERROR_CODES = frozenset(
    {
        "organization_spend_limit_exceeded",
        "project_spend_limit_exceeded",
    }
)


def is_openai_spend_limit_error(exc: HTTPError) -> bool:
    """Return whether an OpenAI HTTP response represents a hard spend limit."""
    if exc.code != 429:
        return False

    try:
        payload = json.loads(exc.read().decode("utf-8"))
    except (AttributeError, UnicodeDecodeError, ValueError, json.JSONDecodeError):
        return False

    error = payload.get("error") if isinstance(payload, dict) else None
    code = error.get("code") if isinstance(error, dict) else None
    return str(code or "").strip() in OPENAI_SPEND_LIMIT_ERROR_CODES
