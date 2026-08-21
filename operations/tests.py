from unittest.mock import patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import Teacher

from .models import HealthCheck, Incident, ManagedProject, ManagedServer, MobileAccessToken, MobileDevice, OperationAction


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
