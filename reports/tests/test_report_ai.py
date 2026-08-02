from __future__ import annotations

import json
from datetime import date
from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse

from reports.report_ai import REPORT_AI_DAILY_LIMIT, report_ai_daily_remaining
from reports.models import (
    Report,
    ReportType,
    PlatformSettings,
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)


class _FakeOpenAIResponse:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return json.dumps(
            {
                "output": [
                    {
                        "type": "message",
                        "role": "assistant",
                        "content": [
                            {
                                "type": "output_text",
                                "text": (
                                    "نُفّذ البرنامج صباح يوم الأحد، واستفاد منه 35 طالبًا، "
                                    "وتضمّن أنشطة توعوية منظمة حققت الأهداف المذكورة."
                                ),
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    OPENAI_API_KEY="test-report-ai-key",
    REPORT_AI_ENABLED=True,
    REPORT_AI_MODEL="gpt-5-nano",
    REPORT_AI_MAX_OUTPUT_TOKENS=700,
    REPORT_AI_TIMEOUT_SECONDS=25,
    RATELIMIT_ENABLE=False,
)
class ReportAIImprovementTests(TestCase):
    def setUp(self):
        cache.clear()
        self.school = School.objects.create(name="مدرسة تحسين التقارير", code="report-ai")
        plan = SubscriptionPlan.objects.create(
            name="خطة تحسين التقارير",
            price=0,
            days_duration=30,
            max_teachers=20,
        )
        SchoolSubscription.objects.create(school=self.school, plan=plan)
        self.teacher = Teacher.objects.create_user(
            phone="500008801",
            name="معلم تحسين التقارير",
            password="test-pass",
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.category = ReportType.objects.create(
            school=self.school,
            code="activity",
            name="نشاط مدرسي",
        )

    def _login(self):
        self.client.force_login(self.teacher)
        session = self.client.session
        session["active_school_id"] = self.school.id
        session.save()

    def test_report_forms_show_ai_improvement_review_controls(self):
        self._login()
        report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="برنامج توعوي",
            report_date=date(2026, 8, 1),
            beneficiaries_count=35,
            idea="تم تنفيذ برنامج توعوي للطلاب.",
            category=self.category,
        )

        for url in (
            reverse("reports:add_report"),
            reverse("reports:edit_my_report", args=[report.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response, "تحسين الصياغة بالذكاء الاصطناعي")
                self.assertContains(response, "3 تحسينات احترافية كل يوم")
                self.assertContains(response, "المتبقي اليوم:")
                self.assertContains(response, "لن يتغير التقرير حتى تعتمد النص")
                self.assertContains(response, reverse("reports:improve_report_text"))
                self.assertContains(response, "css/report-ai-improver.css")
                self.assertContains(response, "js/report-ai-improver.js")

    @override_settings(REPORT_AI_ENABLED=False)
    def test_report_forms_hide_ai_controls_when_service_is_disabled(self):
        self._login()
        report = Report.objects.create(
            school=self.school,
            teacher=self.teacher,
            title="برنامج توعوي",
            report_date=date(2026, 8, 1),
            beneficiaries_count=35,
            idea="تم تنفيذ برنامج توعوي للطلاب.",
            category=self.category,
        )

        for url in (
            reverse("reports:add_report"),
            reverse("reports:edit_my_report", args=[report.pk]),
        ):
            with self.subTest(url=url):
                response = self.client.get(url)
                self.assertEqual(response.status_code, 200)
                self.assertNotContains(response, "تحسين الصياغة بالذكاء الاصطناعي")
                self.assertNotContains(response, "css/report-ai-improver.css")
                self.assertNotContains(response, "js/report-ai-improver.js")

    @patch("reports.report_ai.urlopen")
    def test_platform_switch_hides_and_blocks_report_improvement(self, mocked_urlopen):
        self._login()
        platform_settings = PlatformSettings.get_solo()
        platform_settings.report_ai_enabled = False
        platform_settings.save(update_fields=["report_ai_enabled", "updated_at"])

        page = self.client.get(reverse("reports:add_report"))
        api_response = self.client.post(
            reverse("reports:improve_report_text"),
            data=json.dumps({"text": "هذا نص تقرير مكتمل يحتاج إلى تحسين الصياغة اللغوية."}),
            content_type="application/json",
        )

        self.assertEqual(page.status_code, 200)
        self.assertNotContains(page, "تحسين الصياغة بالذكاء الاصطناعي")
        self.assertNotContains(page, "js/report-ai-improver.js")
        self.assertEqual(api_response.status_code, 404)
        self.assertFalse(api_response.json()["ok"])
        self.assertEqual(report_ai_daily_remaining(self.teacher.pk), REPORT_AI_DAILY_LIMIT)
        mocked_urlopen.assert_not_called()

    def test_improvement_endpoint_requires_login(self):
        response = self.client.post(
            reverse("reports:improve_report_text"),
            data=json.dumps({"text": "نص تقرير مدرسي يحتاج إلى تحسين الصياغة."}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("reports:login"), response.url)

    @patch("reports.report_ai.urlopen", return_value=_FakeOpenAIResponse())
    def test_endpoint_returns_preview_without_saving_or_sending_extra_data(self, mocked_urlopen):
        self._login()
        original = "نفذنا برنامج يوم الأحد واستفاد 35 طالب وكان فيه أنشطة توعوية."

        response = self.client.post(
            reverse("reports:improve_report_text"),
            data=json.dumps({"text": original, "title": "يجب ألا يُرسل"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertEqual(response.json()["remaining"], 2)
        self.assertEqual(response.json()["daily_limit"], REPORT_AI_DAILY_LIMIT)
        self.assertIn("35 طالبًا", response.json()["improved_text"])
        self.assertIn("no-store", response.headers["Cache-Control"])
        self.assertEqual(Report.objects.count(), 0)

        api_request = mocked_urlopen.call_args.args[0]
        request_body = json.loads(api_request.data.decode("utf-8"))
        self.assertEqual(request_body["model"], "gpt-5-nano")
        self.assertEqual(request_body["input"], original)
        self.assertFalse(request_body["store"])
        self.assertIn("لا تخترع", request_body["instructions"])
        self.assertNotIn("يجب ألا يُرسل", api_request.data.decode("utf-8"))
        self.assertNotIn("test-report-ai-key", api_request.data.decode("utf-8"))

    @patch("reports.report_ai.urlopen")
    def test_short_text_is_rejected_without_api_call(self, mocked_urlopen):
        self._login()

        response = self.client.post(
            reverse("reports:improve_report_text"),
            data=json.dumps({"text": "نص قصير"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["ok"])
        self.assertIn("20 حرفًا", response.json()["message"])
        mocked_urlopen.assert_not_called()

    @patch("reports.report_ai.urlopen", return_value=_FakeOpenAIResponse())
    def test_daily_limit_allows_three_successes_then_blocks_without_api_call(self, mocked_urlopen):
        self._login()
        url = reverse("reports:improve_report_text")
        payload = json.dumps(
            {"text": "تم تنفيذ برنامج تدريبي للمعلمين بهدف تحسين الممارسات التعليمية."}
        )

        for expected_remaining in (2, 1, 0):
            response = self.client.post(url, data=payload, content_type="application/json")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["remaining"], expected_remaining)

        limited = self.client.post(url, data=payload, content_type="application/json")

        self.assertEqual(limited.status_code, 429)
        self.assertEqual(limited.json()["remaining"], 0)
        self.assertIn("الثلاثة", limited.json()["message"])
        self.assertEqual(mocked_urlopen.call_count, 3)

    @override_settings(REPORT_AI_ENABLED=False)
    @patch("reports.report_ai.urlopen")
    def test_failed_service_call_does_not_consume_daily_allowance(self, mocked_urlopen):
        self._login()

        response = self.client.post(
            reverse("reports:improve_report_text"),
            data=json.dumps({"text": "هذا نص تقرير مكتمل يحتاج إلى تحسين الصياغة اللغوية."}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(report_ai_daily_remaining(self.teacher.pk), REPORT_AI_DAILY_LIMIT)
        mocked_urlopen.assert_not_called()

    @override_settings(REPORT_AI_ENABLED=False)
    @patch("reports.report_ai.urlopen")
    def test_disabled_feature_returns_safe_service_message(self, mocked_urlopen):
        self._login()

        response = self.client.post(
            reverse("reports:improve_report_text"),
            data=json.dumps({"text": "هذا نص تقرير مكتمل يحتاج إلى تحسين الصياغة اللغوية."}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()["ok"])
        self.assertIn("غير مفعلة", response.json()["message"])
        self.assertEqual(response.json()["remaining"], REPORT_AI_DAILY_LIMIT)
        mocked_urlopen.assert_not_called()
