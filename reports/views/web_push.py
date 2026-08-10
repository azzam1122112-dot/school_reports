from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.http import require_GET, require_POST

from ..models import WebPushSubscription
from ..web_push import save_browser_subscription, web_push_is_configured, web_push_public_key


def _json_body(request: HttpRequest) -> dict:
    if len(request.body) > 16 * 1024:
        raise ValueError("payload_too_large")
    value = json.loads(request.body.decode("utf-8") or "{}")
    if not isinstance(value, dict):
        raise ValueError("payload_invalid")
    return value


@never_cache
@login_required(login_url="reports:login")
@require_GET
def web_push_config(request: HttpRequest) -> JsonResponse:
    enabled = web_push_is_configured()
    return JsonResponse(
        {
            "enabled": enabled,
            "publicKey": web_push_public_key() if enabled else "",
            "activeDevices": WebPushSubscription.objects.filter(
                teacher=request.user,
                is_active=True,
            ).count(),
        }
    )


@never_cache
@login_required(login_url="reports:login")
@require_POST
def web_push_subscribe(request: HttpRequest) -> JsonResponse:
    if not web_push_is_configured():
        return JsonResponse({"ok": False, "error": "push_not_configured"}, status=503)
    try:
        payload = _json_body(request)
        subscription = payload.get("subscription")
        if not isinstance(subscription, dict):
            raise ValueError("subscription_missing")
        saved = save_browser_subscription(
            teacher=request.user,
            subscription=subscription,
            user_agent=request.META.get("HTTP_USER_AGENT", ""),
        )
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    return JsonResponse({"ok": True, "subscriptionId": saved.pk})


@never_cache
@login_required(login_url="reports:login")
@require_POST
def web_push_unsubscribe(request: HttpRequest) -> JsonResponse:
    try:
        endpoint = str(_json_body(request).get("endpoint") or "").strip()
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        return JsonResponse({"ok": False, "error": str(exc)}, status=400)
    if not endpoint:
        return JsonResponse({"ok": False, "error": "endpoint_missing"}, status=400)
    deleted, _ = WebPushSubscription.objects.filter(
        teacher=request.user,
        endpoint=endpoint,
    ).delete()
    return JsonResponse({"ok": True, "deleted": bool(deleted)})
