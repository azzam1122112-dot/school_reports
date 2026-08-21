from __future__ import annotations

from django.contrib.auth import authenticate
from django.db import transaction
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle

from reports.models import TeacherTotpDevice
from reports.totp import decrypt_secret, verify_code

from .authentication import OperationsTokenAuthentication
from .models import Incident, ManagedProject, ManagedServer, MobileAccessToken, MobileDevice, OperationAction
from .serializers import IncidentSerializer, ManagedProjectSerializer, ManagedServerSerializer, MetricSerializer, OperationActionSerializer
from .services import probe_project


class OperationsLoginThrottle(AnonRateThrottle):
    rate = "10/hour"


def _require_superuser(user) -> bool:
    return bool(user and user.is_authenticated and user.is_active and user.is_superuser)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
@throttle_classes([OperationsLoginThrottle])
def login(request):
    phone = str(request.data.get("phone") or "").strip()
    password = str(request.data.get("password") or "")
    user = authenticate(request=request, username=phone, password=password)
    if not _require_superuser(user):
        return Response({"detail": "بيانات الدخول غير صحيحة أو الحساب غير مخول."}, status=status.HTTP_401_UNAUTHORIZED)

    totp_device = TeacherTotpDevice.objects.filter(teacher=user, confirmed_at__isnull=False).first()
    if totp_device is not None:
        secret = decrypt_secret(totp_device.secret_encrypted)
        counter = verify_code(secret or "", str(request.data.get("otp") or ""), last_used_counter=totp_device.last_used_counter)
        if counter is None:
            return Response({"detail": "رمز التحقق مطلوب أو غير صحيح.", "otp_required": True}, status=status.HTTP_401_UNAUTHORIZED)
        with transaction.atomic():
            locked = TeacherTotpDevice.objects.select_for_update().get(pk=totp_device.pk)
            counter = verify_code(secret or "", str(request.data.get("otp") or ""), last_used_counter=locked.last_used_counter)
            if counter is None:
                return Response({"detail": "رمز التحقق استُخدم أو انتهت صلاحيته.", "otp_required": True}, status=status.HTTP_401_UNAUTHORIZED)
            locked.last_used_counter = counter
            locked.last_used_at = timezone.now()
            locked.save(update_fields=("last_used_counter", "last_used_at"))

    token, raw = MobileAccessToken.issue(user=user, device_name=str(request.data.get("device_name") or ""))
    return Response({
        "token": raw,
        "expires_at": token.expires_at,
        "user": {"id": user.pk, "name": user.name, "phone": user.phone},
    })


@api_view(["POST"])
@authentication_classes([OperationsTokenAuthentication])
def logout(request):
    token = request.auth
    token.revoked_at = timezone.now()
    token.save(update_fields=("revoked_at",))
    return Response(status=status.HTTP_204_NO_CONTENT)


@api_view(["GET"])
@authentication_classes([OperationsTokenAuthentication])
def dashboard(request):
    servers = ManagedServer.objects.filter(is_active=True).prefetch_related("projects__services")
    incidents = Incident.objects.filter(status__in=(Incident.Status.OPEN, Incident.Status.ACKNOWLEDGED)).select_related("project")[:20]
    return Response({
        "generated_at": timezone.now(),
        "summary": {
            "servers": servers.count(),
            "projects": ManagedProject.objects.filter(is_active=True).count(),
            "healthy_projects": ManagedProject.objects.filter(is_active=True, status=ManagedProject.Status.HEALTHY).count(),
            "open_incidents": Incident.objects.filter(status__in=(Incident.Status.OPEN, Incident.Status.ACKNOWLEDGED)).count(),
        },
        "servers": ManagedServerSerializer(servers, many=True).data,
        "incidents": IncidentSerializer(incidents, many=True).data,
    })


@api_view(["GET"])
@authentication_classes([OperationsTokenAuthentication])
def project_detail(request, project_id: int):
    project = ManagedProject.objects.select_related("server").prefetch_related("services").filter(pk=project_id, is_active=True).first()
    if project is None:
        return Response({"detail": "المشروع غير موجود."}, status=404)
    checks = project.health_checks.all()[:48]
    actions = project.actions.select_related("requested_by")[:30]
    metrics = project.server.metric_snapshots.all()[:48]
    payload = ManagedProjectSerializer(project).data
    payload.update({
        "server": ManagedServerSerializer(project.server).data,
        "checks": [{"ok": row.ok, "status_code": row.status_code, "latency_ms": row.latency_ms, "error_code": row.error_code, "checked_at": row.checked_at} for row in checks],
        "metrics": MetricSerializer(metrics, many=True).data,
        "actions": OperationActionSerializer(actions, many=True).data,
    })
    return Response(payload)


@api_view(["POST"])
@authentication_classes([OperationsTokenAuthentication])
def create_action(request, project_id: int):
    project = ManagedProject.objects.filter(pk=project_id, is_active=True).first()
    if project is None:
        return Response({"detail": "المشروع غير موجود."}, status=404)
    action_name = str(request.data.get("action") or "")
    allowed = {choice for choice, _ in OperationAction.Action.choices}
    if action_name not in allowed:
        return Response({"detail": "الإجراء غير مسموح."}, status=400)
    destructive = action_name != OperationAction.Action.CHECK_NOW
    if destructive and str(request.data.get("confirmation") or "") != project.slug:
        return Response({"detail": f"اكتب {project.slug} لتأكيد الإجراء.", "confirmation_required": project.slug}, status=409)

    service = None
    if request.data.get("service_id"):
        service = project.services.filter(pk=request.data.get("service_id"), is_active=True).first()
        if service is None:
            return Response({"detail": "الخدمة غير موجودة ضمن المشروع."}, status=400)
        if action_name == OperationAction.Action.RESTART_SERVICE and not service.restart_allowed:
            return Response({"detail": "إعادة تشغيل هذه الخدمة غير مفعلة."}, status=403)

    action = OperationAction.objects.create(project=project, service=service, action=action_name, requested_by=request.user)
    if action_name == OperationAction.Action.CHECK_NOW:
        action.status = OperationAction.Status.RUNNING
        action.started_at = timezone.now()
        action.save(update_fields=("status", "started_at"))
        check = probe_project(project)
        action.status = OperationAction.Status.SUCCEEDED if check.ok else OperationAction.Status.FAILED
        action.result_summary = "اكتمل الفحص بنجاح." if check.ok else f"فشل الفحص: {check.error_code or check.status_code}."
        action.finished_at = timezone.now()
        action.save(update_fields=("status", "result_summary", "finished_at"))
    else:
        action.status = OperationAction.Status.REJECTED
        action.error_code = "agent_not_configured"
        action.result_summary = "يلزم تفعيل وكيل العمليات الآمن على الخادم قبل تنفيذ هذا الإجراء."
        action.finished_at = timezone.now()
        action.save(update_fields=("status", "error_code", "result_summary", "finished_at"))
    return Response(OperationActionSerializer(action).data, status=201)


@api_view(["POST", "DELETE"])
@authentication_classes([OperationsTokenAuthentication])
def device_registration(request):
    device_id = str(request.data.get("device_id") or "").strip()
    if not device_id:
        return Response({"detail": "معرف الجهاز مطلوب."}, status=400)
    if request.method == "DELETE":
        MobileDevice.objects.filter(user=request.user, device_id=device_id).update(is_active=False, fcm_token="")
        return Response(status=204)
    device, _ = MobileDevice.objects.update_or_create(
        user=request.user,
        device_id=device_id[:160],
        defaults={
            "name": str(request.data.get("name") or "")[:120],
            "platform": str(request.data.get("platform") or "android")[:24],
            "fcm_token": str(request.data.get("fcm_token") or ""),
            "alerts_enabled": bool(request.data.get("alerts_enabled", True)),
            "is_active": True,
            "last_seen_at": timezone.now(),
        },
    )
    return Response({"id": device.pk, "alerts_enabled": device.alerts_enabled})


@api_view(["POST"])
@authentication_classes([OperationsTokenAuthentication])
def acknowledge_incident(request, incident_id: int):
    incident = Incident.objects.filter(pk=incident_id, status=Incident.Status.OPEN).first()
    if incident is None:
        return Response({"detail": "التنبيه غير موجود أو تمت معالجته."}, status=404)
    incident.status = Incident.Status.ACKNOWLEDGED
    incident.acknowledged_at = timezone.now()
    incident.acknowledged_by = request.user
    incident.save(update_fields=("status", "acknowledged_at", "acknowledged_by"))
    return Response(IncidentSerializer(incident).data)
