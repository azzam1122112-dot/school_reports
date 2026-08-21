from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import Teacher

from .models import HealthCheck, Incident, ManagedProject, ManagedServer, MobileAccessToken, MobileDevice, OperationAction
from .deployments import DeploymentState
from .services import capture_server_metrics


def deployment_state(**overrides):
    data = {
        "project_id": 1,
        "project_slug": "project",
        "project_name": "Project",
        "repository": "owner/repo",
        "branch": "main",
        "workflow": "ci.yml",
        "configured": True,
        "deployment_enabled": True,
        "latest_sha": "b" * 40,
        "latest_message": "new release",
        "deployed_sha": "a" * 40,
        "deployed_image": "ghcr.io/owner/repo:" + "a" * 40,
        "up_to_date": False,
        "repository_ahead": True,
        "workflow_status": "completed",
        "workflow_conclusion": "success",
        "workflow_url": "https://github.example/run",
        "workflow_run_id": 123,
        "action_required": "اضغط زر النشر.",
        "generated_note": "",
    }
    data.update(overrides)
    return DeploymentState(**data)


@override_settings(DEBUG=True)
class OperationsApiTests(TestCase):
    def setUp(self):
        self.admin = Teacher.objects.create_superuser(phone="0500000001", name="Ops Admin", password="strong-test-password")
        self.regular = Teacher.objects.create_user(phone="0500000002", name="Regular", password="strong-test-password")
        self.server = ManagedServer.objects.create(name="main", slug="main", public_ip="127.0.0.1")
        self.project = ManagedProject.objects.create(
            server=self.server,
            name="Project",
            slug="project",
            base_url="https://example.com",
            health_path="/healthz/",
        )

    def _login(self):
        response = self.client.post(
            reverse("operations:login"),
            {"phone": self.admin.phone, "password": "strong-test-password", "device_name": "test"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 200)
        return response.json()["token"]

    def test_login_is_restricted_to_superusers(self):
        response = self.client.post(
            reverse("operations:login"),
            {"phone": self.regular.phone, "password": "strong-test-password"},
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 401)
        self.assertFalse(MobileAccessToken.objects.filter(user=self.regular).exists())

    def test_dashboard_requires_ops_token_and_returns_inventory(self):
        self.assertEqual(self.client.get(reverse("operations:dashboard")).status_code, 401)
        token = self._login()
        response = self.client.get(reverse("operations:dashboard"), HTTP_AUTHORIZATION=f"Ops-Token {token}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["summary"]["projects"], 1)

    def test_device_registration_never_exposes_other_devices(self):
        token = self._login()
        response = self.client.post(
            reverse("operations:device-registration"),
            {"device_id": "android-test", "name": "Tablet", "fcm_token": "secret-fcm-token"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Ops-Token {token}",
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(MobileDevice.objects.get().user, self.admin)
        self.assertNotIn("fcm_token", response.json())

    @patch("operations.views.probe_project")
    def test_check_now_is_audited(self, probe):
        probe.return_value = HealthCheck(project=self.project, ok=True, latency_ms=12, checked_at=timezone.now())
        token = self._login()
        response = self.client.post(
            reverse("operations:create-action", args=[self.project.pk]),
            {"action": "check_now"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Ops-Token {token}",
        )
        self.assertEqual(response.status_code, 201)
        self.assertEqual(OperationAction.objects.get().status, OperationAction.Status.SUCCEEDED)

    def test_destructive_action_requires_exact_project_confirmation(self):
        token = self._login()
        response = self.client.post(
            reverse("operations:create-action", args=[self.project.pk]),
            {"action": "create_backup", "confirmation": "wrong"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Ops-Token {token}",
        )
        self.assertEqual(response.status_code, 409)
        self.assertFalse(OperationAction.objects.exists())

    def test_acknowledge_incident_records_actor_and_time(self):
        incident = Incident.objects.create(project=self.project, server=self.server, dedupe_key="x", title="Down", message="Unavailable")
        token = self._login()
        response = self.client.post(
            reverse("operations:acknowledge-incident", args=[incident.pk]),
            HTTP_AUTHORIZATION=f"Ops-Token {token}",
        )
        self.assertEqual(response.status_code, 200)
        incident.refresh_from_db()
        self.assertEqual(incident.status, Incident.Status.ACKNOWLEDGED)
        self.assertEqual(incident.acknowledged_by, self.admin)

    @override_settings(OPERATIONS_CAPACITY_SUSTAINED_SAMPLES=3, CPU_ALERT_PERCENT=80)
    @patch("operations.tasks.send_incident_push_task.delay")
    def test_capacity_alert_requires_sustained_pressure(self, push_delay):
        for cpu_percent in (95, 20, 95):
            capture_server_metrics(
                self.server,
                {
                    "cpu_percent": cpu_percent,
                    "memory_percent": 40,
                    "disk_percent": 30,
                    "redis_used_percent": 10,
                    "queue_lengths": {"default": 0},
                },
            )

        self.assertFalse(Incident.objects.filter(dedupe_key=f"server:{self.server.pk}:capacity").exists())
        push_delay.assert_not_called()

    @override_settings(
        OPERATIONS_CAPACITY_SUSTAINED_SAMPLES=3,
        CPU_ALERT_PERCENT=80,
        CELERY_QUEUE_ALERT_LENGTH=10,
    )
    @patch("operations.tasks.send_incident_push_task.delay")
    def test_capacity_alert_includes_recommended_action(self, push_delay):
        for _ in range(3):
            capture_server_metrics(
                self.server,
                {
                    "cpu_percent": 91,
                    "memory_percent": 40,
                    "disk_percent": 30,
                    "redis_used_percent": 10,
                    "queue_lengths": {"images": 12},
                },
            )

        incident = Incident.objects.get(dedupe_key=f"server:{self.server.pk}:capacity")
        self.assertIn("CPU مرتفع بشكل مستمر", incident.message)
        self.assertIn("الإجراء المناسب", incident.message)
        self.assertIn("worker إضافي", incident.message)
        push_delay.assert_called_once_with(incident.pk)

    @patch("operations.views.all_deployment_states")
    def test_deployment_status_reports_repository_drift(self, all_states):
        all_states.return_value = [deployment_state(project_id=self.project.pk)]
        token = self._login()
        response = self.client.get(reverse("operations:deployment-status"), HTTP_AUTHORIZATION=f"Ops-Token {token}")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["repository_ahead_count"], 1)
        self.assertEqual(payload["can_deploy_count"], 1)
        self.assertTrue(payload["deployments"][0]["repository_ahead"])
        self.assertEqual(payload["deployments"][0]["latest_short_sha"], "b" * 12)

    @patch("operations.views.GitHubDeploymentClient")
    def test_trigger_deployment_requires_latest_sha_confirmation(self, client_cls):
        state = deployment_state(project_id=self.project.pk, workflow_url="", workflow_run_id=None)
        client = client_cls.return_value
        client.deployment_state.return_value = state
        token = self._login()

        rejected = self.client.post(
            reverse("operations:trigger-deployment"),
            {"project_id": self.project.pk, "confirmation": "wrong"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Ops-Token {token}",
        )
        self.assertEqual(rejected.status_code, 409)
        client.trigger_deploy.assert_not_called()

        accepted = self.client.post(
            reverse("operations:trigger-deployment"),
            {"project_id": self.project.pk, "confirmation": "b" * 12},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Ops-Token {token}",
        )
        self.assertEqual(accepted.status_code, 202)
        client.trigger_deploy.assert_called_once()
