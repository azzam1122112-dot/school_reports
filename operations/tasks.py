from datetime import timedelta

from celery import shared_task
from django.conf import settings
from django.utils import timezone
from .deployments import GitHubDeploymentClient
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


@shared_task(ignore_result=True)
def monitor_deployment_state_task() -> dict[str, object]:
    if not getattr(settings, "OPERATIONS_DEPLOY_MONITOR_ENABLED", True):
        return {"enabled": False}
    server = ManagedServer.objects.filter(is_active=True).order_by("id").first()
    state = GitHubDeploymentClient().deployment_state()
    key = "deployment:repository-ahead"
    open_incident = Incident.objects.filter(
        dedupe_key=key,
        status__in=(Incident.Status.OPEN, Incident.Status.ACKNOWLEDGED),
    ).first()
    notify_incident = None
    if state.repository_ahead and open_incident is None:
        notify_incident = Incident.objects.create(
            server=server,
            dedupe_key=key,
            title="يوجد إصدار أحدث في المستودع",
            message=(
                f"GitHub main أحدث من الخادم. المستودع: {state.latest_sha[:12]}، "
                f"الخادم: {state.deployed_sha[:12]}. الإجراء المناسب: افتح التطبيق واضغط نشر "
                "لتشغيل GitHub Actions ومتابعة حالة النشر."
            ),
            severity=Incident.Severity.INFO,
        )
    elif not state.repository_ahead and open_incident is not None:
        open_incident.status = Incident.Status.RESOLVED
        open_incident.resolved_at = timezone.now()
        open_incident.save(update_fields=("status", "resolved_at"))
        notify_incident = open_incident
    if notify_incident is not None:
        send_incident_push_task.delay(notify_incident.pk)
    return {
        "enabled": True,
        "repository_ahead": state.repository_ahead,
        "latest_sha": state.latest_sha[:12],
        "deployed_sha": state.deployed_sha[:12],
    }


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
