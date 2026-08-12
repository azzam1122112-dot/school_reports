from __future__ import annotations

from datetime import timedelta
from time import monotonic

from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.cache import cache
from django.db import connection, models
from django.db.models import Count, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.utils import timezone

from core import opmetrics

from ..models import Assignment, AuditLog, Report, ShareLink, Ticket


def _probe(label: str, operation) -> dict[str, object]:
    started = monotonic()
    try:
        operation()
        return {
            "label": label,
            "ok": True,
            "status": "يعمل",
            "latency_ms": round((monotonic() - started) * 1000),
        }
    except Exception:
        return {
            "label": label,
            "ok": False,
            "status": "غير متاح",
            "latency_ms": None,
        }


def _database_probe() -> None:
    with connection.cursor() as cursor:
        cursor.execute("SELECT 1")
        cursor.fetchone()


def _cache_probe() -> None:
    key = "platform-operations-probe"
    cache.set(key, "ok", timeout=10)
    if cache.get(key) != "ok":
        raise RuntimeError("cache probe mismatch")


def _metric_value(metrics: dict[str, int], name: str) -> int:
    return int(metrics.get(name, 0) or 0)


@login_required(login_url="reports:platform_login")
@user_passes_test(
    lambda user: getattr(user, "is_superuser", False),
    login_url="reports:platform_login",
)
def platform_operations(request: HttpRequest) -> HttpResponse:
    """Human-readable platform monitoring for live metrics and audit history."""
    try:
        period = int(request.GET.get("days", "7"))
    except (TypeError, ValueError):
        period = 7
    if period not in {1, 7, 30}:
        period = 7

    now = timezone.now()
    since = now - timedelta(days=period)
    metrics = opmetrics.snapshot()
    requests_total = _metric_value(metrics, "http.requests.total")
    responses_4xx = _metric_value(metrics, "http.responses.4xx")
    responses_5xx = _metric_value(metrics, "http.responses.5xx")
    degraded_total = _metric_value(metrics, "degraded:_total")
    duration_count = _metric_value(metrics, "http.response.duration.count")
    duration_sum = _metric_value(metrics, "http.response.duration.sum_ms")
    average_latency = round(duration_sum / duration_count) if duration_count else 0

    audit = AuditLog.objects.filter(timestamp__gte=since)
    audit_summary = audit.aggregate(
        actions=Count("id"),
        logins=Count("id", filter=models.Q(action=AuditLog.Action.LOGIN)),
        unique_users=Count("teacher_id", distinct=True),
    )

    feature_labels = {
        "Report": "التقارير",
        "Ticket": "الطلبات والدعم",
        "Assignment": "التكليفات",
        "TeacherAchievementFile": "ملفات الإنجاز",
        "Notification": "الإشعارات والتعاميم",
        "SchoolMembership": "إدارة المستخدمين",
        "StaffScope": "الصلاحيات",
        "PlatformEmail": "بريد المنصة",
        "PlatformEmailConfiguration": "إعدادات البريد",
    }
    feature_counts = {
        row["model_name"]: row["total"]
        for row in (
            audit.exclude(model_name="Auth")
            .values("model_name")
            .annotate(total=Count("id"))
            .order_by("-total")
        )
    }
    feature_usage = [
        {"label": label, "count": feature_counts.get(model_name, 0)}
        for model_name, label in feature_labels.items()
    ]

    created_counts = {
        "reports": Report.objects.filter(created_at__gte=since).count(),
        "tickets": Ticket.objects.filter(created_at__gte=since).count(),
        "assignments": Assignment.objects.filter(created_at__gte=since).count(),
        "links": ShareLink.objects.filter(created_at__gte=since).count(),
        "link_opens": ShareLink.objects.filter(created_at__gte=since).aggregate(
            total=Sum("access_count")
        )["total"]
        or 0,
    }

    probes = [
        _probe("قاعدة البيانات", _database_probe),
        _probe("التخزين المؤقت", _cache_probe),
    ]
    alerts = []
    if responses_5xx:
        alerts.append({"tone": "critical", "text": f"رُصدت {responses_5xx} استجابة خادم فاشلة خلال الساعة الحالية."})
    if degraded_total:
        alerts.append({"tone": "warning", "text": f"أكملت المنصة {degraded_total} عملية بقيمة بديلة؛ راجع سجل Sentry والسجلات."})
    if average_latency >= 800:
        alerts.append({"tone": "warning", "text": f"متوسط زمن الاستجابة مرتفع حاليًا: {average_latency} مللي ثانية."})
    for probe in probes:
        if not probe["ok"]:
            alerts.append({"tone": "critical", "text": f"فحص {probe['label']} غير ناجح."})
    if not alerts:
        alerts.append({"tone": "healthy", "text": "لا توجد مؤشرات تشغيل حرجة في الساعة الحالية."})

    recent_failures = [
        {"label": name.removeprefix("degraded:"), "count": value}
        for name, value in sorted(metrics.items(), key=lambda item: item[1], reverse=True)
        if name.startswith("degraded:") and name != "degraded:_total"
    ][:8]

    return render(
        request,
        "reports/platform_operations.html",
        {
            "active": "platform_operations",
            "period": period,
            "bucket": opmetrics._now_bucket(),
            "requests_total": requests_total,
            "responses_4xx": responses_4xx,
            "responses_5xx": responses_5xx,
            "degraded_total": degraded_total,
            "average_latency": average_latency,
            "audit_summary": audit_summary,
            "feature_usage": feature_usage,
            "created_counts": created_counts,
            "probes": probes,
            "alerts": alerts,
            "recent_failures": recent_failures,
            "refreshed_at": timezone.localtime(now),
        },
    )
