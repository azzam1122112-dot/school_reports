from __future__ import annotations

import logging
import secrets

from django.core.cache import cache
from django.http import HttpResponseNotFound

from .trace_context import reset_trace_id, set_trace_id
from . import opmetrics

BLOCKED_PREFIXES = (
    "/wp-admin",
    "/wp-content",
    "/wp-includes",
    "/wordpress",
    "/.env",
    "/lander",
    "/cmd_sco",
    "/xmlrpc.php",
    "/vendor/phpunit",
    "/cgi-bin",
    "/boaform",
    "/manager/html",
    "/invoker",
)

BLOCKED_CONTAINS = (
    "jmxinvokerservlet",
    "struts",
    "autodiscover.xml",
    "/.git/",
)

NOISY_PREFIX_LIMITS = {
    "/.well-known/": {"window": 120, "burst": 12},
    "/rest/": {"window": 60, "burst": 20},
}


logger = logging.getLogger(__name__)


class RequestTraceMiddleware:
    """Attach a request correlation id and expose it in response headers/log context."""

    HEADER_NAME = "X-Request-ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(self.HEADER_NAME) if hasattr(request, "headers") else None
        trace_id = incoming or secrets.token_hex(8)
        request.trace_id = str(trace_id)[:64]
        token = set_trace_id(request.trace_id)
        try:
            response = self.get_response(request)
        finally:
            reset_trace_id(token)
        try:
            response[self.HEADER_NAME] = request.trace_id
        except Exception:
            pass
        return response


class BlockBadPathsMiddleware:
    """Blocks common scanner/probe paths early before reaching views/DB."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = (request.path or "/").lower()
        if path.startswith("/static/") or path.startswith("/media/"):
            return self.get_response(request)
        noisy_rule = None
        for prefix, rule in NOISY_PREFIX_LIMITS.items():
            if path.startswith(prefix):
                noisy_rule = rule
                break
        if noisy_rule:
            try:
                ip = (
                    request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                    or request.META.get("REMOTE_ADDR", "-")
                )
                key = f"noise-limit:{prefix}:{ip}"
                first_seen = cache.add(key, 0, timeout=int(noisy_rule["window"]))
                count = cache.incr(key) if not first_seen else 1
                if count > int(noisy_rule["burst"]):
                    opmetrics.increment("http.noisy_path.rate_limited")
                    if count in {int(noisy_rule["burst"]) + 1, int(noisy_rule["burst"]) + 10}:
                        logger.info(
                            "Rate-limited noisy path=%s ip=%s trace_id=%s count=%s",
                            path,
                            ip,
                            getattr(request, "trace_id", "-"),
                            count,
                        )
                    response = HttpResponseNotFound()
                    response.status_code = 429
                    return response
            except Exception:
                pass

        blocked = False
        for pref in BLOCKED_PREFIXES:
            if path.startswith(pref):
                blocked = True
                break
        if not blocked:
            for marker in BLOCKED_CONTAINS:
                if marker in path:
                    blocked = True
                    break

        if blocked:
            try:
                ip = (
                    request.META.get("HTTP_X_FORWARDED_FOR", "").split(",")[0].strip()
                    or request.META.get("REMOTE_ADDR", "-")
                )
                opmetrics.increment("http.scanner.blocked")
                key = f"scan-block:{ip}:{path[:80]}"
                first_seen = cache.add(key, 1, timeout=300)
                if first_seen:
                    logger.warning(
                        "Blocked suspicious probe path=%s ip=%s trace_id=%s ua=%s",
                        path,
                        ip,
                        getattr(request, "trace_id", "-"),
                        (request.META.get("HTTP_USER_AGENT", "") or "-")[:180],
                    )
            except Exception:
                pass
            # Return 404 to minimize endpoint fingerprinting.
            return HttpResponseNotFound()
        return self.get_response(request)
