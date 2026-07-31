from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import PlatformSettings, Teacher


@override_settings(ALLOWED_HOSTS=["testserver"])
class MaintenanceModeTests(TestCase):
    def setUp(self):
        cache.delete("platform_maintenance_state_v1")
        self.settings_obj = PlatformSettings.get_solo()
        self.settings_obj.maintenance_mode_enabled = True
        self.settings_obj.maintenance_message = "نعود قريباً بإذن الله."
        self.settings_obj.save()

    def tearDown(self):
        cache.delete("platform_maintenance_state_v1")

    def test_application_pages_show_maintenance_screen(self):
        response = self.client.get("/home/")

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "الموقع تحت الصيانة والتطوير", status_code=503)
        self.assertContains(response, "نعود قريباً بإذن الله.", status_code=503)

    def test_markdown_message_is_rendered_cleanly(self):
        self.settings_obj.maintenance_message = "# 🚧 نعمل على تحسين تجربتكم\n\nمنصة **نوافذ** غير متاحة مؤقتًا.\n\n**فريق منصة توثيق**"
        self.settings_obj.save()

        response = self.client.get("/home/")
        html = response.content.decode("utf-8", errors="ignore")

        self.assertEqual(response.status_code, 503)
        self.assertIn("<h1>🚧 نعمل على تحسين تجربتكم</h1>", html)
        self.assertIn("<strong>نوافذ</strong>", html)
        self.assertNotIn("# 🚧", html)
        self.assertNotIn("**", html)

    def test_public_compliance_pages_remain_available(self):
        for path in (
            "/",
            reverse("reports:faq"),
            reverse("reports:privacy_policy"),
            reverse("reports:terms_conditions"),
            reverse("reports:refund_policy"),
            reverse("reports:service_delivery_policy"),
            reverse("reports:complaints_policy"),
        ):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)

    def test_api_requests_receive_maintenance_json(self):
        response = self.client.get("/api/v1/not-found/", HTTP_ACCEPT="application/json")

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()["detail"], "maintenance_mode")

    def test_login_and_superuser_access_remain_available(self):
        login_response = self.client.get(reverse("reports:login"))
        self.assertEqual(login_response.status_code, 200)

        admin = Teacher.objects.create_superuser(
            phone="599123456",
            name="Platform Admin",
            password="pass",
        )
        self.client.force_login(admin)

        response = self.client.get(reverse("reports:platform_settings"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "وضع الصيانة والتطوير")
