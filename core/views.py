"""Lightweight operational endpoints (health checks, metrics snapshot).

These bypass the full middleware chain by being placed early in urlpatterns
and returning simple JSON/text responses.
"""
from __future__ import annotations

import os
import logging
from urllib.parse import urlencode

from django.contrib import messages
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.cache import never_cache
from django.views.csrf import csrf_failure as django_csrf_failure

logger = logging.getLogger(__name__)


def csrf_failure(request, reason=""):
    """Refresh stale login forms without weakening CSRF checks elsewhere."""
    login_paths = {
        reverse("reports:login"),
        reverse("reports:platform_login"),
    }
    if request.path not in login_paths:
        return django_csrf_failure(request, reason=reason)

    next_value = str(request.POST.get("next") or "").strip()
    if next_value and not url_has_allowed_host_and_scheme(
        next_value,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_value = ""

    logger.info(
        "Stale login CSRF token refreshed path=%s trace_id=%s",
        request.path,
        getattr(request, "trace_id", None),
    )
    messages.error(
        request,
        "انتهت صلاحية صفحة الدخول. حدّثنا الصفحة برمز أمان جديد؛ أدخل بياناتك مرة أخرى.",
    )
    target = request.path
    if next_value:
        target = f"{target}?{urlencode({'next': next_value})}"
    response = redirect(target)
    response["Cache-Control"] = "no-store"
    return response


def _env_bool(name: str, default: bool = False) -> bool:
    raw = (os.getenv(name) or "").strip().lower()
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


@never_cache
def healthz(request):
    """Minimal health/readiness probe for load balancers and uptime monitors.

    Checks:
    - Database connectivity (single lightweight query)
    - Cache/Redis reachability (ping via set/get)
    - Channel layer reachability (optional, best-effort)

    Returns 200 if all critical checks pass, 503 otherwise.
    """
    checks: dict[str, str] = {}
    healthy = True

    # ── Database ──
    try:
        from django.db import connection
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        checks["db"] = "ok"
    except Exception as exc:
        logger.warning("Health check database probe failed (%s)", type(exc).__name__)
        checks["db"] = "error"
        healthy = False

    # ── Cache (Redis) ──
    try:
        from django.core.cache import cache
        cache.set("_healthz", 1, timeout=10)
        val = cache.get("_healthz")
        if val == 1:
            checks["cache"] = "ok"
        else:
            checks["cache"] = "error"
            healthy = False
    except Exception as exc:
        logger.warning("Health check cache probe failed (%s)", type(exc).__name__)
        checks["cache"] = "error"
        healthy = False

    # ── Channel Layer (optional, best-effort) ──
    # A full Channels send/receive probe creates Redis channel-layer traffic.
    # Keep it out of high-frequency platform health checks unless explicitly
    # enabled for diagnostics.
    if _env_bool("HEALTHZ_CHECK_CHANNELS", False):
        try:
            from channels.layers import get_channel_layer
            layer = get_channel_layer()
            if layer is not None:
                import asyncio

                async def _probe():
                    await layer.send("_healthz_probe", {"type": "healthz"})
                    await layer.receive("_healthz_probe")

                loop = asyncio.new_event_loop()
                try:
                    loop.run_until_complete(asyncio.wait_for(_probe(), timeout=2.0))
                finally:
                    loop.close()
                checks["channels"] = "ok"
            else:
                checks["channels"] = "not_configured"
        except Exception as exc:
            # Channel layer failure is non-critical (WebSocket only)
            logger.warning("Health check channel-layer probe failed (%s)", type(exc).__name__)
            checks["channels"] = "degraded"
    else:
        checks["channels"] = "skipped"

    status_code = 200 if healthy else 503
    response = JsonResponse({
        "status": "ok" if healthy else "error",
        "checks": checks,
    }, status=status_code)
    response["Cache-Control"] = "no-store"
    return response


@never_cache
def ops_metrics(request):
    """Return current opmetrics counters plus infrastructure stats. Superuser only."""
    user = getattr(request, "user", None)
    if not (
        user
        and getattr(user, "is_authenticated", False)
        and getattr(user, "is_superuser", False)
    ):
        return JsonResponse({"detail": "forbidden"}, status=403)

    from core import opmetrics as _opm
    data = _opm.snapshot()

    infra: dict[str, object] = {}

    # ── DB connection info ──
    try:
        from django.db import connection
        infra["db_vendor"] = connection.vendor
        infra["db_conn_max_age"] = getattr(connection.settings_dict, "CONN_MAX_AGE", None) or connection.settings_dict.get("CONN_MAX_AGE")
        infra["db_connection_usable"] = bool(connection.is_usable())
    except Exception:
        infra["db_probe"] = "unavailable"

    try:
        import os as _os
        import resource as _resource

        infra["process_cpu_seconds"] = round(float(_resource.getrusage(_resource.RUSAGE_SELF).ru_utime), 3)
        infra["process_ram_mb"] = round(float(_resource.getrusage(_resource.RUSAGE_SELF).ru_maxrss) / 1024, 1)
        infra["pid"] = _os.getpid()
    except (ImportError, OSError, ValueError):
        # ``resource`` غير متاح على ويندوز — غيابُ المقياس متوقَّع لا عطل.
        infra["process_rusage"] = "unavailable"

    try:
        import psutil  # type: ignore

        proc = psutil.Process()
        infra["process_cpu_percent"] = proc.cpu_percent(interval=0.0)
        infra["process_rss_mb"] = round(proc.memory_info().rss / (1024 * 1024), 1)
    except Exception:
        # ``psutil`` اعتمادية اختيارية.
        infra["process_psutil"] = "unavailable"

    # ── Redis key estimate (cache DB) ──
    try:
        from django_redis import get_redis_connection
        redis_conn = get_redis_connection("default")
        info = redis_conn.info(section="keyspace")
        # info looks like {"db1": {"keys": 123, ...}}
        total_keys = sum(v.get("keys", 0) for v in info.values() if isinstance(v, dict))
        infra["redis_cache_keys"] = total_keys
        mem_info = redis_conn.info(section="memory")
        infra["redis_used_memory_mb"] = round(mem_info.get("used_memory", 0) / (1024 * 1024), 1)
        client_info = redis_conn.info(section="clients")
        infra["redis_connected_clients"] = client_info.get("connected_clients")
    except Exception:
        infra["redis_cache_keys"] = "unavailable"

    try:
        from django.core.cache import cache

        infra["ws_active_connections"] = int(cache.get("ws:gauge:active") or 0)
    except Exception:
        infra["ws_active_connections"] = "unavailable"

    # ── Celery queue lengths (best-effort via Redis LLEN) ──
    try:
        from django.conf import settings as _settings
        broker_url = getattr(_settings, "CELERY_BROKER_URL", "") or ""
        if "redis" in broker_url:
            import redis as _redis
            r = _redis.from_url(broker_url)
            for q in ("default", "notifications", "images", "periodic"):
                infra[f"queue_len_{q}"] = r.llen(q)
    except Exception:
        # طولُ الطابور مقياسٌ تشغيلي: «غير متاح» جوابٌ صادق، والصمت ليس كذلك.
        infra["queue_lengths"] = "unavailable"

    response = JsonResponse({
        "bucket": _opm._now_bucket(),
        "metrics": data,
        "infra": infra,
    })
    response["Cache-Control"] = "no-store"
    return response
