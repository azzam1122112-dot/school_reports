from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from .models import HealthCheck, Incident, ManagedServer, OperationAction, ServerMetricSnapshot
from .push import send_incident_push
from .services import capture_server_metrics, probe_all_projects


@shared_task(ignore_result=True)
def run_operations_monitor_task() -> dict[str, int]:
    checks = probe_all_projects()
    return {"checked": len(checks), "failed": sum(1 for check in checks if not check.ok)}


@shared_task(ignore_result=True)
def store_capacity_snapshot_task(report: dict) -> None:
    server = ManagedServer.objects.filter(is_active=True).order_by("id").first()
    if server is not None:
        capture_server_metrics(server, report)


@shared_task(ignore_result=True, autoretry_for=(Exception,), retry_backoff=True, retry_kwargs={"max_retries": 3})
def send_incident_push_task(incident_id: int) -> dict[str, int]:
    incident = Incident.objects.filter(pk=incident_id).first()
    return send_incident_push(incident) if incident is not None else {"sent": 0, "failed": 0, "disabled": 0}


@shared_task(ignore_result=True)
def cleanup_operations_history_task() -> dict[str, int]:
    retention_days = max(7, int(getattr(settings, "OPERATIONS_HISTORY_RETENTION_DAYS", 30) or 30))
    cutoff = timezone.now() - timedelta(days=retention_days)
    checks, _ = HealthCheck.objects.filter(checked_at__lt=cutoff).delete()
    metrics, _ = ServerMetricSnapshot.objects.filter(captured_at__lt=cutoff).delete()
    actions, _ = OperationAction.objects.filter(requested_at__lt=cutoff).delete()
    return {"health_checks": checks, "metrics": metrics, "actions": actions}
