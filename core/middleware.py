from __future__ import annotations

import logging
import secrets
import threading

from django.conf import settings
from django.core.cache import cache
from django.http import HttpResponse, HttpResponseNotFound, JsonResponse

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


class ConcurrencyLimitMiddleware:
    """Shed excess load instead of letting a spike exhaust the process.

    Why this exists
    ---------------
    Django's ASGI handler wraps every request in ``ThreadSensitiveContext``,
    and asgiref then allocates a dedicated ``ThreadPoolExecutor(max_workers=1)``
    per in-flight request. There is no global ceiling: 1,000 simultaneous
    visitors become 1,000 OS threads, each opening its own database connection
    (the connection registry is thread-local, so nothing is shared). PostgreSQL
    refuses new connections past ``max_connections`` — default 100 — so an
    unbounded burst turns a slow page into a full outage, and Celery and the
    beat scheduler lose their connections too.

    ``gunicorn --threads`` does not help: the Uvicorn worker ignores it. So the
    ceiling has to live in the application.

    Behaviour
    ---------
    Requests beyond ``MAX_CONCURRENT_REQUESTS`` get a fast 503 with
    ``Retry-After`` rather than queueing behind a saturated process. Serving a
    subset of visitors correctly beats timing out for all of them.

    ``/healthz/`` is always admitted: a merely busy container must not be
    reported as dead and restarted, which would make the overload worse.
    """

    EXEMPT_PATHS = frozenset({"/healthz/"})

    _lock = threading.Lock()
    _in_flight = 0

    def __init__(self, get_response):
        self.get_response = get_response

    @classmethod
    def _limit(cls) -> int:
        try:
            return max(0, int(getattr(settings, "MAX_CONCURRENT_REQUESTS", 0) or 0))
        except (TypeError, ValueError):
            return 0

    @classmethod
    def in_flight(cls) -> int:
        with cls._lock:
            return cls._in_flight

    @staticmethod
    def _wants_json(request) -> bool:
        try:
            path = (getattr(request, "path", "") or "").lower()
            accept = (request.headers.get("Accept") or "").lower()
            requested_with = (request.headers.get("X-Requested-With") or "").lower()
            return (
                path.startswith("/api/")
                or "application/json" in accept
                or requested_with == "xmlhttprequest"
            )
        except Exception:
            return False

    def _overloaded_response(self, request):
        retry_after = str(int(getattr(settings, "OVERLOAD_RETRY_AFTER_SECONDS", 5) or 5))
        message = "الخدمة مزدحمة حالياً. حاول مرة أخرى بعد لحظات."
        if self._wants_json(request):
            response = JsonResponse({"detail": "server_busy", "message": message}, status=503)
        else:
            response = HttpResponse(
                message, status=503, content_type="text/plain; charset=utf-8"
            )
        response["Retry-After"] = retry_after
        response["Cache-Control"] = "no-store"
        return response

    def __call__(self, request):
        limit = self._limit()
        path = getattr(request, "path", "") or ""
        if limit <= 0 or path in self.EXEMPT_PATHS or path.startswith(("/static/", "/media/")):
            return self.get_response(request)

        cls = type(self)
        with cls._lock:
            if cls._in_flight >= limit:
                current = cls._in_flight
                admitted = False
            else:
                cls._in_flight += 1
                current = cls._in_flight
                admitted = True

        if not admitted:
            opmetrics.increment("http.overload.shed")
            logger.warning(
                "Shedding request over concurrency limit path=%s in_flight=%s limit=%s trace_id=%s",
                path,
                current,
                limit,
                getattr(request, "trace_id", "-"),
            )
            return self._overloaded_response(request)

        try:
            return self.get_response(request)
        finally:
            with cls._lock:
                cls._in_flight -= 1


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
