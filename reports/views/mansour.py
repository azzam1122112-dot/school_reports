from __future__ import annotations

import json

from django.conf import settings
from django.http import HttpRequest, JsonResponse
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST
from django_ratelimit.decorators import ratelimit

from ..mansour_assistant import (
    MansourAssistantError,
    ask_mansour,
    infer_public_audience,
    normalise_audience,
)
from ..mansour_knowledge import (
    AUDIENCE_GENERAL,
    AUDIENCE_LABELS,
    AUDIENCE_MANAGER,
    AUDIENCE_PLATFORM_SUPERVISOR,
    AUDIENCE_REPORT_SUPERVISOR,
    AUDIENCE_TEACHER,
    PUBLIC_AUDIENCES,
)
from ..models import SubscriptionPlan
from ..ai_features import (
    FEATURE_INTERNAL_HELP,
    FEATURE_MANSOUR_PUBLIC,
    platform_ai_toggle_enabled,
)
from ..permissions import (
    is_platform_admin,
    is_report_viewer_for_school,
    is_school_manager,
)
from ._helpers import _get_active_school


def _json_response(payload: dict, *, status: int = 200) -> JsonResponse:
    return JsonResponse(
        payload,
        status=status,
        json_dumps_params={"ensure_ascii": False},
    )


def _resolve_audience(request: HttpRequest, requested_audience, question="", history=None) -> str:
    """Resolve trusted account roles server-side; visitors may select a public role."""
    user = getattr(request, "user", None)
    if not getattr(user, "is_authenticated", False):
        audience = normalise_audience(requested_audience)
        if audience in PUBLIC_AUDIENCES and audience != AUDIENCE_GENERAL:
            return audience
        inferred = infer_public_audience(question)
        if inferred != AUDIENCE_GENERAL:
            return inferred
        if isinstance(history, list):
            for message in reversed(history[-6:]):
                if not isinstance(message, dict) or message.get("role") != "user":
                    continue
                inferred = infer_public_audience(message.get("content"))
                if inferred != AUDIENCE_GENERAL:
                    return inferred
        return AUDIENCE_GENERAL

    if getattr(user, "is_superuser", False) or is_platform_admin(user):
        return AUDIENCE_PLATFORM_SUPERVISOR

    active_school = _get_active_school(request)
    if is_school_manager(user, active_school=active_school):
        return AUDIENCE_MANAGER
    if is_report_viewer_for_school(user, active_school=active_school):
        return AUDIENCE_REPORT_SUPERVISOR
    return AUDIENCE_TEACHER


@csrf_exempt
@never_cache
@require_POST
@ratelimit(key="ip", rate="50/d", method="POST", block=False)
def mansour_assistant_reply(request: HttpRequest) -> JsonResponse:
    user = getattr(request, "user", None)
    feature = (
        FEATURE_INTERNAL_HELP
        if getattr(user, "is_authenticated", False)
        else FEATURE_MANSOUR_PUBLIC
    )
    if not platform_ai_toggle_enabled(feature):
        return _json_response(
            {"ok": False, "message": "المساعد غير متاح حالياً."},
            status=404,
        )

    if getattr(request, "limited", False):
        return _json_response(
            {
                "ok": False,
                "message": "وصلت إلى الحد اليومي البالغ 50 سؤالًا. يمكنك استخدام منصور مجددًا غدًا.",
            },
            status=429,
        )

    if request.content_type != "application/json":
        return _json_response(
            {"ok": False, "message": "صيغة الطلب غير صحيحة."},
            status=415,
        )

    try:
        payload = json.loads(request.body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return _json_response(
            {"ok": False, "message": "تعذر قراءة الاستفسار."},
            status=400,
        )

    if not isinstance(payload, dict):
        return _json_response(
            {"ok": False, "message": "صيغة الطلب غير صحيحة."},
            status=400,
        )

    plans = list(
        SubscriptionPlan.objects.filter(is_active=True)
        .order_by("price", "max_teachers", "days_duration", "id")
        .values("name", "price", "days_duration", "max_teachers")
    )
    serialised_plans = []
    seen_free_trials = set()
    for plan in plans:
        price = f"{plan['price']:.2f}".rstrip("0").rstrip(".")
        if price == "0":
            free_trial_key = (plan["days_duration"], plan["max_teachers"])
            if free_trial_key in seen_free_trials:
                continue
            seen_free_trials.add(free_trial_key)
        serialised_plans.append({**plan, "price": price})

    audience = _resolve_audience(
        request,
        payload.get("audience"),
        payload.get("question"),
        payload.get("history"),
    )

    try:
        answer, sources = ask_mansour(
            payload.get("question"),
            history=payload.get("history"),
            plans=serialised_plans,
            audience=audience,
            page_context=(
                payload.get("page_context")
                if getattr(request.user, "is_authenticated", False)
                else None
            ),
        )
    except MansourAssistantError as exc:
        status = (
            503
            if not getattr(settings, "MANSOUR_ASSISTANT_ENABLED", False)
            or not getattr(settings, "OPENAI_API_KEY", "")
            else 400
        )
        return _json_response({"ok": False, "message": str(exc)}, status=status)

    return _json_response(
        {
            "ok": True,
            "answer": answer,
            "sources": sources,
            "audience": audience,
            "audience_label": AUDIENCE_LABELS[audience],
        }
    )
