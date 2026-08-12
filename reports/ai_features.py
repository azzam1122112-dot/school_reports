"""Runtime feature switches for the platform's AI-assisted UI.

The environment variables remain the infrastructure-level safety switch.  The
database switches below let a platform administrator show or hide individual
features without rebuilding or restarting the application.
"""

from __future__ import annotations

from django.core.cache import cache


FEATURE_MANSOUR_PUBLIC = "mansour_public"
FEATURE_REPORT_IMPROVEMENT = "report_improvement"
FEATURE_INTERNAL_HELP = "internal_help"
FEATURE_VOICE_REPORT = "voice_report"

CACHE_KEY = "platform_ai_feature_toggles_v1"
CACHE_TIMEOUT_SECONDS = 15

_DEFAULTS = {
    FEATURE_MANSOUR_PUBLIC: True,
    FEATURE_REPORT_IMPROVEMENT: True,
    FEATURE_INTERNAL_HELP: True,
    FEATURE_VOICE_REPORT: True,
}

_MODEL_FIELDS = {
    FEATURE_MANSOUR_PUBLIC: "mansour_public_enabled",
    FEATURE_REPORT_IMPROVEMENT: "report_ai_enabled",
    FEATURE_INTERNAL_HELP: "internal_ai_help_enabled",
    FEATURE_VOICE_REPORT: "voice_report_enabled",
}


def clear_platform_ai_feature_cache() -> None:
    """Make saved platform switches visible to all workers immediately."""

    try:
        cache.delete(CACHE_KEY)
    except Exception:
        pass


def get_platform_ai_feature_toggles() -> dict[str, bool]:
    """Return the three visual feature switches with safe upgrade defaults."""

    try:
        cached = cache.get(CACHE_KEY)
        if isinstance(cached, dict) and all(key in cached for key in _DEFAULTS):
            return {key: bool(cached[key]) for key in _DEFAULTS}
    except Exception:
        pass

    toggles = dict(_DEFAULTS)
    try:
        from .models import PlatformSettings

        row = (
            PlatformSettings.objects.order_by("id")
            .values(*_MODEL_FIELDS.values())
            .first()
        )
        if row:
            for feature, field_name in _MODEL_FIELDS.items():
                toggles[feature] = bool(row.get(field_name))
    except Exception:
        # During a rolling migration older application instances may briefly
        # run before the new columns exist. Preserve the pre-feature behavior.
        toggles = dict(_DEFAULTS)

    try:
        cache.set(CACHE_KEY, toggles, CACHE_TIMEOUT_SECONDS)
    except Exception:
        pass
    return toggles


def platform_ai_toggle_enabled(feature: str) -> bool:
    if feature not in _DEFAULTS:
        return False
    return bool(get_platform_ai_feature_toggles().get(feature, False))


def ai_feature_flags(request) -> dict[str, bool]:
    """Expose visual switches to public and authenticated templates."""

    toggles = get_platform_ai_feature_toggles()
    return {
        "AI_MANSOUR_PUBLIC_ENABLED": toggles[FEATURE_MANSOUR_PUBLIC],
        "AI_REPORT_IMPROVEMENT_ENABLED": toggles[FEATURE_REPORT_IMPROVEMENT],
        "AI_INTERNAL_HELP_ENABLED": toggles[FEATURE_INTERNAL_HELP],
        "AI_VOICE_REPORT_ENABLED": toggles[FEATURE_VOICE_REPORT],
    }
