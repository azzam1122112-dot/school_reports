from __future__ import annotations

import json
import logging
import socket
import time
from urllib.parse import urlsplit
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from .models import HealthCheck, Incident, ManagedProject, ManagedServer, ServerMetricSnapshot

logger = logging.getLogger(__name__)


def _safe_error_code(exc: Exception) -> str:
    if isinstance(exc, HTTPError):
        return f"http_{exc.code}"
    if isinstance(exc, (TimeoutError, socket.timeout)):
        return "timeout"
    if isinstance(exc, URLError):
        return "connection_error"
    return "probe_error"


def probe_project(project: ManagedProject) -> HealthCheck:
    started = time.monotonic()
    status_code = None
    summary = ""
    error_code = ""
    ok = False
    timeout = float(getattr(settings, "OPERATIONS_PROBE_TIMEOUT_SECONDS", 8) or 8)
    try:
        scheme = urlsplit(project.health_url).scheme.lower()
        if scheme not in ({"http", "https"} if settings.DEBUG else {"https"}):
            raise ValueError("unsupported_health_url_scheme")
        request = Request(  # noqa: S310 - scheme allowlisted above
            project.health_url,
            headers={"Accept": "application/json", "User-Agent": "TawtheeqOperations/1.0"},
            method="GET",
        )
        with urlopen(request, timeout=max(1.0, min(timeout, 20.0))) as response:  # noqa: S310 - scheme allowlisted above
            status_code = int(response.status)
            raw = response.read(2048).decode("utf-8", errors="replace")
            summary = " ".join(raw.split())[:300]
            ok = status_code == project.expected_status
    except Exception as exc:
        error_code = _safe_error_code(exc)
        summary = type(exc).__name__
        if isinstance(exc, HTTPError):
            status_code = exc.code
        logger.info("Operations health probe failed project=%s code=%s", project.slug, error_code)

    latency_ms = max(0, int((time.monotonic() - started) * 1000))
    now = timezone.now()
    with transaction.atomic():
        check = HealthCheck.objects.create(
            project=project,
            ok=ok,
            status_code=status_code,
            latency_ms=latency_ms,
            error_code=error_code,
            response_summary=summary,
            checked_at=now,
        )
        locked = ManagedProject.objects.select_for_update().get(pk=project.pk)
        failures = 0 if ok else locked.consecutive_failures + 1
        status = ManagedProject.Status.HEALTHY if ok else (
            ManagedProject.Status.DOWN if failures >= 2 else ManagedProject.Status.DEGRADED
        )
        ManagedProject.objects.filter(pk=project.pk).update(
            status=status,
            last_latency_ms=latency_ms,
            last_checked_at=now,
            consecutive_failures=failures,
        )

        key = f"project:{project.pk}:health"
        open_incident = Incident.objects.filter(dedupe_key=key, status__in=(Incident.Status.OPEN, Incident.Status.ACKNOWLEDGED)).first()
        new_incident = None
        if not ok and failures >= 2 and open_incident is None:
            new_incident = Incident.objects.create(
                project=project,
                server=project.server,
                dedupe_key=key,
                title=f"تعذر الوصول إلى {project.name}",
                message=f"فشل فحص الصحة مرتين متتاليتين. الرمز: {error_code or status_code or 'unknown'}.",
                severity=Incident.Severity.CRITICAL,
            )
        elif ok and open_incident is not None:
            open_incident.status = Incident.Status.RESOLVED
            open_incident.resolved_at = now
            open_incident.save(update_fields=("status", "resolved_at"))
            new_incident = open_incident
    if new_incident is not None and project.alerts_enabled:
        from .tasks import send_incident_push_task

        send_incident_push_task.delay(new_incident.pk)
    return check


def probe_all_projects() -> list[HealthCheck]:
    return [probe_project(project) for project in ManagedProject.objects.filter(is_active=True).select_related("server")]


def capture_server_metrics(server: ManagedServer, report: dict) -> ServerMetricSnapshot:
    now = timezone.now()
    snapshot = ServerMetricSnapshot.objects.create(
        server=server,
        cpu_percent=report.get("cpu_percent"),
        memory_percent=report.get("memory_percent"),
        disk_percent=report.get("disk_percent"),
        redis_memory_percent=report.get("redis_used_percent"),
        queue_lengths=report.get("queue_lengths") or {},
        captured_at=now,
    )
    values = [report.get("cpu_percent"), report.get("memory_percent"), report.get("disk_percent")]
    thresholds = [
        int(getattr(settings, "CPU_ALERT_PERCENT", 85)),
        int(getattr(settings, "MEMORY_ALERT_PERCENT", 85)),
        int(getattr(settings, "DISK_ALERT_PERCENT", 80)),
    ]
    known = [(float(value), threshold) for value, threshold in zip(values, thresholds, strict=True) if value is not None]
    status = ManagedServer.Status.DEGRADED if any(value >= threshold for value, threshold in known) else ManagedServer.Status.HEALTHY
    ManagedServer.objects.filter(pk=server.pk).update(
        status=status,
        cpu_percent=report.get("cpu_percent"),
        memory_percent=report.get("memory_percent"),
        disk_percent=report.get("disk_percent"),
        last_checked_at=now,
    )
    key = f"server:{server.pk}:capacity"
    open_incident = Incident.objects.filter(
        dedupe_key=key,
        status__in=(Incident.Status.OPEN, Incident.Status.ACKNOWLEDGED),
    ).first()
    notify_incident = None
    alerts = list(report.get("alerts") or [])
    if alerts and open_incident is None:
        notify_incident = Incident.objects.create(
            server=server,
            dedupe_key=key,
            title=f"ضغط مرتفع على {server.name}",
            message=" ".join(str(alert) for alert in alerts)[:2000],
            severity=Incident.Severity.WARNING,
        )
    elif not alerts and open_incident is not None:
        open_incident.status = Incident.Status.RESOLVED
        open_incident.resolved_at = now
        open_incident.save(update_fields=("status", "resolved_at"))
        notify_incident = open_incident
    if notify_incident is not None:
        from .tasks import send_incident_push_task

        send_incident_push_task.delay(notify_incident.pk)
    return snapshot
