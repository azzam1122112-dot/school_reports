"""Lightweight operational endpoints (health checks, metrics snapshot).

These bypass the full middleware chain by being placed early in urlpatterns
and returning simple JSON/text responses.
"""
from __future__ import annotations

import os
import time
import logging

from django.http import JsonResponse

logger = logging.getLogger(__name__)


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
        t0 = time.monotonic()
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
        db_ms = round((time.monotonic() - t0) * 1000, 1)
        checks["db"] = f"ok ({db_ms}ms)"
    except Exception as exc:
        checks["db"] = f"error: {exc}"
        healthy = False

    # ── Cache (Redis) ──
    try:
        from django.core.cache import cache
        t0 = time.monotonic()
        cache.set("_healthz", 1, timeout=10)
        val = cache.get("_healthz")
        cache_ms = round((time.monotonic() - t0) * 1000, 1)
        if val == 1:
            checks["cache"] = f"ok ({cache_ms}ms)"
        else:
            checks["cache"] = "error: read-back mismatch"
            healthy = False
    except Exception as exc:
        checks["cache"] = f"error: {exc}"
        healthy = False

    # ── Channel Layer (best-effort) ──
    try:
        from channels.layers import get_channel_layer
        layer = get_channel_layer()
        if layer is not None:
            import asyncio

            async def _probe():
                await layer.send("_healthz_probe", {"type": "healthz"})
                await layer.receive("_healthz_probe")

            t0 = time.monotonic()
            loop = asyncio.new_event_loop()
            try:
                loop.run_until_complete(asyncio.wait_for(_probe(), timeout=2.0))
            finally:
                loop.close()
            ch_ms = round((time.monotonic() - t0) * 1000, 1)
            checks["channels"] = f"ok ({ch_ms}ms)"
        else:
            checks["channels"] = "not configured"
    except Exception as exc:
        # Channel layer failure is non-critical (WebSocket only)
        checks["channels"] = f"degraded: {exc}"

    status_code = 200 if healthy else 503
    return JsonResponse({
        "status": "ok" if healthy else "error",
        "instance": os.getenv("RENDER_INSTANCE_ID", os.getenv("HOSTNAME", "local")),
        "checks": checks,
    }, status=status_code)


def ops_metrics(request):
    """Return current opmetrics counters plus infrastructure stats. Superuser-only."""
    user = getattr(request, "user", None)
    if not (user and getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False)):
        return JsonResponse({"detail": "forbidden"}, status=403)

    from core import opmetrics as _opm
    data = _opm.snapshot()

    infra: dict[str, object] = {}

    # ── DB connection info ──
    try:
        from django.db import connection
        infra["db_vendor"] = connection.vendor
        infra["db_conn_max_age"] = getattr(connection.settings_dict, "CONN_MAX_AGE", None) or connection.settings_dict.get("CONN_MAX_AGE")
    except Exception:
        pass

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
    except Exception:
        infra["redis_cache_keys"] = "unavailable"

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
        pass

    return JsonResponse({
        "bucket": _opm._now_bucket(),
        "metrics": data,
        "infra": infra,
    })
