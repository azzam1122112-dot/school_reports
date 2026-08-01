from __future__ import annotations

import json
from unittest.mock import patch
from urllib.error import URLError

from django.conf import settings
from django.test import Client, RequestFactory, TestCase, override_settings
from django.urls import reverse

from reports.mansour_assistant import (
    INTENT_GENERAL,
    _fails_customer_service_guard,
    _instructions,
    _offline_customer_reply,
    _sanitise_answer_text,
    select_knowledge,
)
from reports.models import (
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.views.mansour import _resolve_audience


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
                                "text": "نعم، يمكنك بدء تجربة مجانية ثم اختيار الباقة المناسبة.",
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")


class _FakeWeakComplaintOpenAIResponse:
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
                                "text": "أفضل بداية في سؤالك الحالي هي تسجيل الدخول من الصفحة الرئيسية.",
                            }
                        ],
                    }
                ]
            },
            ensure_ascii=False,
        ).encode("utf-8")


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    OPENAI_API_KEY="test-secret-key",
    MANSOUR_ASSISTANT_ENABLED=True,
    MANSOUR_ASSISTANT_MODEL="gpt-5-nano",
    RATELIMIT_ENABLE=False,
)
class MansourAssistantTests(TestCase):
    def setUp(self):
        SubscriptionPlan.objects.create(
            name="باقة المدرسة",
            price=650,
            days_duration=180,
            max_teachers=50,
        )

    def test_landing_includes_accessible_mansour_widget(self):
        response = self.client.get(reverse("reports:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'id="mansourLauncher"')
        self.assertContains(response, 'id="mansourPanel"')
        self.assertContains(response, "منصور")
        self.assertNotContains(response, "اختر دورك")
        self.assertNotContains(response, "أسئلة مقترحة")
        self.assertNotContains(response, 'data-mansour-audience="teacher"')
        self.assertContains(response, "اكتب سؤالك هنا")
        self.assertContains(response, reverse("reports:mansour_assistant_reply"))
        self.assertContains(response, "css/mansour-assistant.css")
        self.assertContains(response, "js/mansour-assistant.js")

    def test_system_prompt_enforces_customer_service_persona(self):
        prompt = _instructions(
            select_knowledge("كيف أضيف تقرير جديد؟", audience="teacher"),
            [],
            audience="teacher",
        )

        self.assertIn("ممثل خدمة العملاء", prompt)
        self.assertIn("تصرّف كممثل خدمة عملاء فقط", prompt)

    @override_settings(OPENAI_API_KEY="", MANSOUR_ASSISTANT_ENABLED=True)
    def test_endpoint_uses_local_fallback_when_key_missing(self):
        response = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps(
                {
                    "question": "السلام عليكم",
                    "history": [],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("ممثل خدمة العملاء", payload["answer"])

    @override_settings(OPENAI_API_KEY="", MANSOUR_ASSISTANT_ENABLED=True)
    def test_complaint_intent_returns_professional_fallback(self):
        response = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps(
                {
                    "question": "ارغب بتقديم شكوى",
                    "history": [],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("نعتذر", payload["answer"])
        self.assertIn("رقم متابعة", payload["answer"])
        self.assertTrue(payload["sources"])
        self.assertEqual(payload["sources"][0]["url"], "/complaints/#complaint-form")

    @patch("reports.mansour_assistant.urlopen", return_value=_FakeOpenAIResponse())
    def test_endpoint_calls_responses_api_server_side(self, mocked_urlopen):
        response = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps(
                {
                    "question": "كيف أشترك؟",
                    "history": [{"role": "assistant", "content": "أهلًا بك"}],
                    "audience": "manager",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("تجربة مجانية", response.json()["answer"])

        request = mocked_urlopen.call_args.args[0]
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertEqual(request_body["model"], "gpt-5-nano")
        self.assertEqual(
            request_body["reasoning"],
            {"effort": settings.MANSOUR_ASSISTANT_REASONING_EFFORT},
        )
        self.assertEqual(
            request_body["max_output_tokens"],
            settings.MANSOUR_ASSISTANT_MAX_OUTPUT_TOKENS,
        )
        self.assertFalse(request_body["store"])
        self.assertIn("باقة المدرسة", request_body["instructions"])
        self.assertIn("650", request_body["instructions"])
        self.assertIn("الفئة: مدير مدرسة", request_body["instructions"])
        self.assertIn("guide/#manager-communication", request_body["instructions"])
        self.assertNotIn("test-secret-key", request.data.decode("utf-8"))
        self.assertEqual(response.json()["audience"], "manager")
        self.assertEqual(response.json()["audience_label"], "مدير مدرسة")

    @patch("reports.mansour_assistant.urlopen", return_value=_FakeWeakComplaintOpenAIResponse())
    def test_complaint_intent_quality_guard_rewrites_weak_model_answer(self, _mocked_urlopen):
        response = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps({"question": "أرغب بتقديم شكوى", "history": []}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("رقم متابعة", payload["answer"])
        self.assertEqual(payload["sources"][0]["url"], "/complaints/#complaint-form")

    def test_retrieval_is_scoped_to_the_selected_role(self):
        manager_items = select_knowledge(
            "كيف أرسل تعميمًا إلى قسمين؟",
            audience="manager",
        )
        teacher_items = select_knowledge(
            "كيف أتعامل مع التعميم؟",
            audience="teacher",
        )

        manager_slugs = {item.slug for item in manager_items}
        teacher_slugs = {item.slug for item in teacher_items}
        self.assertIn("manager-communication", manager_slugs)
        self.assertNotIn("manager-communication", teacher_slugs)
        self.assertIn("teacher-circulars", teacher_slugs)

    @override_settings(OPENAI_API_KEY="", MANSOUR_ASSISTANT_ENABLED=True)
    def test_visitor_stated_manager_role_gets_manager_workflow(self):
        response = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps(
                {"question": "أنا مدير مدرسة وأريد إضافة المعلمين وإرسال تعميم، من أين أبدأ؟"}
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["audience"], "manager")
        self.assertIn("إدارة المعلمين والأقسام", payload["answer"])
        self.assertIn("الإشعارات والتعاميم", payload["answer"])
        self.assertNotIn("خطوات التسجيل", payload["answer"])

    @override_settings(OPENAI_API_KEY="", MANSOUR_ASSISTANT_ENABLED=True)
    def test_follow_up_reuses_previous_question_for_retrieval(self):
        response = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps(
                {
                    "question": "ما فهمت، اشرحها لي باختصار",
                    "history": [
                        {
                            "role": "user",
                            "content": "أنا مدير مدرسة وأريد إضافة المعلمين وإرسال تعميم، من أين أبدأ؟",
                        }
                    ],
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["audience"], "manager")
        self.assertIn("إدارة المعلمين والأقسام", response.json()["answer"])
        self.assertNotIn("التعريف بمنصة توثيق", response.json()["answer"])
        self.assertIn("باختصار", response.json()["answer"])
        self.assertEqual(len(response.json()["sources"]), 2)

    @override_settings(OPENAI_API_KEY="", MANSOUR_ASSISTANT_ENABLED=True)
    def test_pricing_reply_deduplicates_equivalent_free_trials(self):
        SubscriptionPlan.objects.create(
            name="تجربة مجانية",
            price=0,
            days_duration=14,
            max_teachers=5,
        )

        response = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps({"question": "كم سعر الاشتراك وهل توجد تجربة مجانية؟"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["answer"].count("0 ريال لمدة 14 يوم"), 1)

    def test_generated_links_are_removed_from_answer_text(self):
        answer = _sanitise_answer_text(
            "راجع الدليل: /guide/#teacher-report\n"
            "أو [صفحة الاشتراك](/subscription/my/#archiveOrder)، "
            "ولا تستخدم https://example.com/private."
        )

        self.assertNotIn("/guide/", answer)
        self.assertNotIn("/subscription/", answer)
        self.assertNotIn("https://", answer)
        self.assertNotIn(":  (", answer)
        self.assertIn("صفحة الاشتراك", answer)

    def test_offline_general_reply_does_not_start_with_stale_phrase(self):
        selected = select_knowledge("كيف أرسل تعميمًا؟", audience="manager")

        answer = _offline_customer_reply(
            "كيف أرسل تعميمًا؟",
            intent=INTENT_GENERAL,
            selected=selected,
            plans=[],
        )

        self.assertNotIn("الخطوة الصحيحة في حالتك", answer)

    def test_quality_guard_rejects_stale_opening_phrase(self):
        answer = "الخطوة الصحيحة في حالتك: ابدأ من الإعدادات."

        self.assertTrue(_fails_customer_service_guard(answer, intent=INTENT_GENERAL))

    def test_authenticated_manager_role_overrides_client_claim(self):
        school = School.objects.create(name="مدرسة منصور", code="mansour-school")
        manager = Teacher.objects.create_user(
            phone="500009901",
            name="مدير منصور",
            password="test-pass",
        )
        SchoolMembership.objects.create(
            school=school,
            teacher=manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        request = RequestFactory().post("/assistant/mansour/")
        request.user = manager
        request.session = {"active_school_id": school.id}
        request.active_school = school

        self.assertEqual(_resolve_audience(request, "teacher"), "manager")

    def test_server_distinguishes_teacher_and_both_supervisor_types(self):
        school = School.objects.create(name="مدرسة الأدوار", code="mansour-roles")
        SchoolSubscription.objects.create(
            school=school,
            plan=SubscriptionPlan.objects.get(name="باقة المدرسة"),
        )
        teacher = Teacher.objects.create_user(
            phone="500009902",
            name="معلم منصور",
            password="test-pass",
        )
        report_supervisor = Teacher.objects.create_user(
            phone="500009903",
            name="مشرف تقارير منصور",
            password="test-pass",
        )
        platform_supervisor = Teacher.objects.create_user(
            phone="500009904",
            name="مشرف منصة منصور",
            password="test-pass",
            is_platform_admin=True,
        )
        SchoolMembership.objects.create(
            school=school,
            teacher=teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        SchoolMembership.objects.create(
            school=school,
            teacher=report_supervisor,
            role_type=SchoolMembership.RoleType.REPORT_VIEWER,
        )

        def resolved_for(user):
            request = RequestFactory().post("/assistant/mansour/")
            request.user = user
            request.session = {"active_school_id": school.id}
            request.active_school = school
            return _resolve_audience(request, "manager")

        self.assertEqual(resolved_for(teacher), "teacher")
        self.assertEqual(resolved_for(report_supervisor), "report_supervisor")
        self.assertEqual(resolved_for(platform_supervisor), "platform_supervisor")

    @patch("reports.mansour_assistant.urlopen", return_value=_FakeOpenAIResponse())
    def test_anonymous_user_cannot_claim_an_internal_supervisor_role(self, mocked_urlopen):
        response = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps(
                {
                    "question": "ما صلاحياتي؟",
                    "audience": "platform_supervisor",
                }
            ),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["audience"], "general")
        request = mocked_urlopen.call_args.args[0]
        request_body = json.loads(request.data.decode("utf-8"))
        self.assertIn("الفئة: زائر", request_body["instructions"])

    @patch("reports.mansour_assistant.urlopen", return_value=_FakeOpenAIResponse())
    def test_public_endpoint_does_not_depend_on_a_csrf_cookie(self, _mocked_urlopen):
        csrf_client = Client(enforce_csrf_checks=True)

        response = csrf_client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps({"question": "كيف أشترك؟"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_endpoint_rejects_invalid_and_long_questions_without_api_call(self):
        invalid = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data="not-json",
            content_type="application/json",
        )
        too_long = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps({"question": "س" * 501}),
            content_type="application/json",
        )

        self.assertEqual(invalid.status_code, 400)
        self.assertEqual(too_long.status_code, 400)
        self.assertFalse(too_long.json()["ok"])

    @override_settings(OPENAI_API_KEY="", MANSOUR_ASSISTANT_ENABLED=False)
    def test_endpoint_uses_local_fallback_when_assistant_is_not_configured(self):
        response = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps({"question": "كيف أشترك؟"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["ok"])
        self.assertIn("التجربة المجانية", response.json()["answer"])
        self.assertNotIn("OPENAI", response.content.decode("utf-8"))

    @patch("reports.mansour_assistant.urlopen", side_effect=URLError("down"))
    def test_endpoint_falls_back_when_openai_is_temporarily_unreachable(self, _mocked_urlopen):
        response = self.client.post(
            reverse("reports:mansour_assistant_reply"),
            data=json.dumps({"question": "كيف اسجل في المنصة؟"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["ok"])
        self.assertIn("التسجيل", payload["answer"])
