from rest_framework import serializers

from .models import HealthCheck, Incident, ManagedProject, ManagedServer, ManagedService, OperationAction, ServerMetricSnapshot


class ManagedServiceSerializer(serializers.ModelSerializer):
    kind_label = serializers.CharField(source="get_kind_display", read_only=True)

    class Meta:
        model = ManagedService
        fields = ("id", "name", "service_key", "kind", "kind_label", "status", "last_checked_at", "restart_allowed")


class ManagedProjectSerializer(serializers.ModelSerializer):
    services = ManagedServiceSerializer(many=True, read_only=True)
    health_url = serializers.CharField(read_only=True)

    class Meta:
        model = ManagedProject
        fields = (
            "id", "name", "slug", "base_url", "health_url", "status", "last_latency_ms",
            "last_checked_at", "consecutive_failures", "alerts_enabled", "services",
        )


class ManagedServerSerializer(serializers.ModelSerializer):
    projects = ManagedProjectSerializer(many=True, read_only=True)

    class Meta:
        model = ManagedServer
        fields = (
            "id", "name", "slug", "provider", "provider_server_id", "public_ip", "region",
            "server_type", "status", "cpu_percent", "memory_percent", "disk_percent",
            "last_checked_at", "projects",
        )


class IncidentSerializer(serializers.ModelSerializer):
    project_name = serializers.CharField(source="project.name", default="", read_only=True)

    class Meta:
        model = Incident
        fields = ("id", "title", "message", "severity", "status", "project_name", "opened_at", "acknowledged_at", "resolved_at")


class HealthCheckSerializer(serializers.ModelSerializer):
    class Meta:
        model = HealthCheck
        fields = ("id", "ok", "status_code", "latency_ms", "error_code", "checked_at")


class MetricSerializer(serializers.ModelSerializer):
    class Meta:
        model = ServerMetricSnapshot
        fields = ("cpu_percent", "memory_percent", "disk_percent", "redis_memory_percent", "queue_lengths", "captured_at")


class OperationActionSerializer(serializers.ModelSerializer):
    action_label = serializers.CharField(source="get_action_display", read_only=True)
    requested_by_name = serializers.CharField(source="requested_by.name", read_only=True)

    class Meta:
        model = OperationAction
        fields = (
            "id", "request_id", "action", "action_label", "status", "requested_by_name",
            "result_summary", "error_code", "requested_at", "started_at", "finished_at",
        )
