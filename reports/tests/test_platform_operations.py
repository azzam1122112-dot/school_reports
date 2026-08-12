from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from core import opmetrics
from reports.models import Teacher


@override_settings(ALLOWED_HOSTS=["testserver"])
class PlatformOperationsTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = Teacher.objects.create_superuser(
            name="مدير مراقبة المنصة",
            phone="0509400001",
            email="ops@example.com",
            password="StrongPass123!",
        )
        self.user = Teacher.objects.create_user(
            name="مستخدم عادي",
            phone="0509400002",
            email="normal-ops@example.com",
            password="StrongPass123!",
        )

    def test_superuser_sees_live_metrics_and_service_probes(self):
        opmetrics.increment("http.requests.total", 4)
        opmetrics.increment("http.responses.5xx")
        opmetrics.timing("http.response.duration", 250)
        self.client.force_login(self.admin)

        response = self.client.get(reverse("reports:platform_operations"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "مراقبة المنصة")
        self.assertContains(response, "قاعدة البيانات")
        self.assertContains(response, "أخطاء خادم 5xx")
        self.assertContains(response, "250 ms")

    def test_non_superuser_cannot_open_monitoring_dashboard(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("reports:platform_operations"))

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("reports:platform_login"), response.url)

    def test_invalid_period_falls_back_to_seven_days(self):
        self.client.force_login(self.admin)

        response = self.client.get(reverse("reports:platform_operations"), {"days": "999"})

        self.assertEqual(response.context["period"], 7)
