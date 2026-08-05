from datetime import timedelta
from decimal import Decimal
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from reports.models import (
    ArchiveStorageOption,
    AuditLog,
    Payment,
    PlatformSettings,
    School,
    Teacher,
)


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "platform-admin-experience-tests",
        }
    },
)
class PlatformAdminExperienceTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = Teacher.objects.create_superuser(
            phone="599700001",
            name="مدير النظام",
            password="pass",
        )
        self.school = School.objects.create(
            name="مدرسة الاختبار",
            code="platform-experience-school",
            storage_used_bytes=900 * 1024 * 1024,
        )
        self.client.force_login(self.admin)

    def tearDown(self):
        cache.clear()

    def test_dashboard_uses_platform_identity_and_accessible_controls(self):
        response = self.client.get(reverse("reports:platform_admin_dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "إدارة المنصة")
        self.assertContains(response, "مركز التشغيل")
        self.assertContains(response, 'id="dashboardStatus"')
        self.assertContains(response, 'aria-controls="searchResults"')
        self.assertContains(response, "التخزين المستخدم")
        self.assertNotContains(response, ">تقاريري</a>")

    def test_period_revenue_uses_payment_date_not_record_creation_date(self):
        current_payment = Payment.objects.create(
            school=self.school,
            amount=Decimal("125.00"),
            payment_date=timezone.localdate(),
            status=Payment.Status.APPROVED,
            created_by=self.admin,
        )
        Payment.objects.filter(pk=current_payment.pk).update(
            created_at=timezone.now() - timedelta(days=120)
        )

        old_payment = Payment.objects.create(
            school=self.school,
            amount=Decimal("900.00"),
            payment_date=timezone.localdate() - timedelta(days=120),
            status=Payment.Status.APPROVED,
            created_by=self.admin,
        )
        Payment.objects.filter(pk=old_payment.pk).update(created_at=timezone.now())

        response = self.client.get(
            reverse("reports:api_platform_dashboard_data"),
            {"period": "month", "refresh": "1"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["kpis"]["total_revenue"], 125.0)

    def test_force_refresh_bypasses_dashboard_cache(self):
        endpoint = reverse("reports:api_platform_dashboard_data")
        first = self.client.get(endpoint, {"period": "all"}).json()

        School.objects.create(name="مدرسة جديدة", code="new-platform-school")

        cached = self.client.get(endpoint, {"period": "all"}).json()
        refreshed = self.client.get(
            endpoint,
            {"period": "all", "refresh": "1"},
        ).json()

        self.assertEqual(cached["kpis"]["schools_total"], first["kpis"]["schools_total"])
        self.assertEqual(
            refreshed["kpis"]["schools_total"],
            first["kpis"]["schools_total"] + 1,
        )

    def test_dashboard_reports_storage_risk(self):
        response = self.client.get(
            reverse("reports:api_platform_dashboard_data"),
            {"period": "all", "refresh": "1"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["kpis"]["storage_used_bytes"], 900 * 1024 * 1024)
        self.assertEqual(payload["kpis"]["storage_near_limit"], 1)

    def test_school_delete_requires_typed_name_and_keeps_audit_record(self):
        delete_url = reverse("reports:school_delete", args=[self.school.pk])

        confirmation = self.client.get(delete_url)
        self.assertEqual(confirmation.status_code, 200)
        self.assertContains(confirmation, "حذف المدرسة نهائيًا")
        self.assertContains(confirmation, self.school.name)

        rejected = self.client.post(delete_url, {"confirm_name": "اسم غير مطابق"})
        self.assertEqual(rejected.status_code, 400)
        self.assertTrue(School.objects.filter(pk=self.school.pk).exists())

        school_id = self.school.pk
        accepted = self.client.post(delete_url, {"confirm_name": self.school.name})
        self.assertRedirects(accepted, reverse("reports:schools_admin_list"))
        self.assertFalse(School.objects.filter(pk=school_id).exists())

        audit = AuditLog.objects.filter(
            action=AuditLog.Action.DELETE,
            model_name="School",
            object_id=school_id,
        ).first()
        self.assertIsNotNone(audit)
        self.assertIsNone(audit.school_id)
        self.assertEqual(audit.changes["school_code"], "platform-experience-school")

    def test_school_operations_redirect_to_platform_login(self):
        self.client.logout()

        response = self.client.get(reverse("reports:schools_admin_list"))

        self.assertRedirects(
            response,
            f"{reverse('reports:platform_login')}?next={reverse('reports:schools_admin_list')}",
            fetch_redirect_response=False,
        )

    def test_platform_can_open_mansour_content_editor_page(self):
        response = self.client.get(reverse("reports:platform_mansour_content"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "محرر محتوى منصور")
        self.assertContains(response, "حفظ المحتوى")

    def test_platform_settings_can_control_ai_feature_visibility_independently(self):
        page = self.client.get(reverse("reports:platform_settings"))

        self.assertEqual(page.status_code, 200)
        self.assertContains(page, "ظهور ميزات الذكاء الاصطناعي")
        self.assertContains(page, "المساعد منصور")
        self.assertContains(page, "تحسين التقارير")
        self.assertContains(page, "المساعدة داخل النظام")

        settings_obj = PlatformSettings.get_solo()
        option = ArchiveStorageOption.objects.order_by("id").first()
        self.assertIsNotNone(option)
        response = self.client.post(
            reverse("reports:platform_settings"),
            data={
                "maintenance_message": settings_obj.maintenance_message,
                "report_ai_enabled": "on",
                "archive_addon_annual_price": str(settings_obj.archive_addon_annual_price),
                "archive_included_storage_gb": str(settings_obj.archive_included_storage_gb),
                "storage_mb_per_teacher": str(settings_obj.storage_mb_per_teacher),
                "free_storage_mb": str(settings_obj.free_storage_mb),
                "storage_options-TOTAL_FORMS": "1",
                "storage_options-INITIAL_FORMS": "1",
                "storage_options-MIN_NUM_FORMS": "0",
                "storage_options-MAX_NUM_FORMS": "1000",
                "storage_options-0-id": str(option.pk),
                "storage_options-0-storage_gb": str(option.storage_gb),
                "storage_options-0-price": str(option.price),
                "storage_options-0-sort_order": str(option.sort_order),
                "storage_options-0-is_active": "on",
            },
        )

        self.assertRedirects(response, reverse("reports:platform_settings"))
        settings_obj.refresh_from_db()
        self.assertFalse(settings_obj.mansour_public_enabled)
        self.assertTrue(settings_obj.report_ai_enabled)
        self.assertFalse(settings_obj.internal_ai_help_enabled)

    def test_platform_can_save_mansour_content_json(self):
        payload = {
            "role_guidance": {
                "general": "توجيه عام",
                "teacher": "توجيه معلم",
                "manager": "توجيه مدير",
            },
            "role_default_slugs": {
                "general": ["sample"],
                "teacher": ["sample"],
                "manager": ["sample"],
            },
            "knowledge_items": [
                {
                    "slug": "sample",
                    "title": "عنوان",
                    "url": "/guide/#sample",
                    "text": "نص المعرفة",
                    "topics": ["موضوع"],
                    "audiences": ["teacher"],
                    "keywords": "كلمات",
                    "priority": 3,
                }
            ],
        }

        with TemporaryDirectory() as temp_dir:
            file_path = Path(temp_dir) / "mansour_knowledge_content.json"
            file_path.write_text("{}", encoding="utf-8")

            with patch("reports.views.subscriptions.MANSOUR_KNOWLEDGE_CONTENT_PATH", file_path):
                with patch("reports.mansour_assistant.reload_mansour_knowledge_runtime") as reload_mock:
                    response = self.client.post(
                        reverse("reports:platform_mansour_content"),
                        data={"content": json.dumps(payload, ensure_ascii=False)},
                    )

                    self.assertEqual(response.status_code, 302)
                    self.assertEqual(response.url, reverse("reports:platform_mansour_content"))

                    saved = json.loads(file_path.read_text(encoding="utf-8"))
                    self.assertEqual(saved["knowledge_items"][0]["slug"], "sample")
                    reload_mock.assert_called_once()

    def test_school_manager_creation_requires_email(self):
        response = self.client.post(
            reverse("reports:school_manager_create"),
            data={
                "name": "مدير بدون بريد",
                "phone": "0551234511",
                "email": "",
                "password": "ManagerPass#2026",
                "is_active": "on",
                "schools": [str(self.school.id)],
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "هذا الحقل مطلوب")
        self.assertFalse(Teacher.objects.filter(phone="0551234511").exists())
