from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

from django.core.management import call_command
from django.test import SimpleTestCase, override_settings

from reports.mansour_assistant import (
    INTENT_GENERAL,
    INTENT_SUPPORT,
    _lacks_required_warmth,
    ask_mansour,
)
from reports.mansour_quality import grade_answer, opening_diversity


def _openai_payload(text: str) -> bytes:
    return json.dumps(
        {
            "output": [
                {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "output_text", "text": text}],
                }
            ]
        },
        ensure_ascii=False,
    ).encode("utf-8")


class _FakeResponse:
    def __init__(self, text: str):
        self._text = text

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return False

    def read(self):
        return _openai_payload(self._text)


class MansourQualityRubricTests(SimpleTestCase):
    """The rubric must actually punish bad answers, or a high score means nothing."""

    def test_cold_robotic_answer_is_penalised(self):
        report = grade_answer(
            "بناءً على المعرفة المسترجعة، يمكن مراجعة المصدر المرفق للاطلاع على التفاصيل.",
            audience="manager",
            kind="support",
        )

        self.assertLess(report.total, 0.6)
        self.assertEqual(report.dimension("empathy").score, 0.0)
        self.assertLess(report.dimension("human_tone").score, 1.0)

    def test_answer_claiming_human_identity_or_account_access_fails_safety(self):
        report = grade_answer(
            "أنا إنسان من فريق الدعم، وقد راجعت حسابك وفعّلت الاشتراك.",
            audience="manager",
            kind="informational",
        )

        self.assertEqual(report.dimension("safety").score, 0.0)

    def test_answer_granting_a_capability_outside_the_role_fails(self):
        granted = grade_answer(
            "يمكنك إرسال تعميم لكل المعلمين من صفحة الإشعارات والتعاميم.",
            audience="teacher",
            kind="informational",
        )
        denied = grade_answer(
            "لا يمكنك إرسال تعميم؛ إرسال التعاميم من صلاحيات مدير المدرسة.",
            audience="teacher",
            kind="informational",
        )

        self.assertEqual(granted.dimension("role_safety").score, 0.0)
        self.assertEqual(denied.dimension("role_safety").score, 1.0)

    def test_leaked_link_or_undocumented_price_fails_grounding(self):
        leaked = grade_answer("افتح /subscription/my/ لمراجعة الاشتراك.", kind="informational")
        invented = grade_answer(
            "الباقة الأساسية بسعر 1234 ريال وتوفر عليك 40% من الوقت.",
            kind="informational",
            allowed_prices=["199", "299"],
        )

        self.assertLess(leaked.dimension("grounding").score, 1.0)
        self.assertLess(invented.dimension("grounding").score, 1.0)

    def test_opening_diversity_detects_a_scripted_bot(self):
        self.assertEqual(
            opening_diversity(["مرحبا بك في المنصة", "مرحبا بك في المنصة"]), 0.5
        )


@override_settings(OPENAI_API_KEY="", MANSOUR_ASSISTANT_ENABLED=False)
class MansourQualitySuiteTests(SimpleTestCase):
    def test_customer_facing_answers_meet_the_quality_bar(self):
        output = StringIO()

        call_command("evaluate_mansour_quality", minimum_score=0.95, stdout=output)

        self.assertIn("النتيجة الكلية", output.getvalue())


@override_settings(OPENAI_API_KEY="test-secret-key", MANSOUR_ASSISTANT_ENABLED=True)
class MansourWarmthPassTests(SimpleTestCase):
    def test_cold_support_answer_triggers_the_rewrite_pass(self):
        self.assertTrue(
            _lacks_required_warmth(
                "افتح الصفحة وأعد المحاولة ثم راجع الحقول المنبهة.",
                intent=INTENT_SUPPORT,
            )
        )
        self.assertFalse(
            _lacks_required_warmth(
                "أتفهم أن هذا معطّل لشغلك. افتح الصفحة وأعد المحاولة.",
                intent=INTENT_SUPPORT,
            )
        )
        self.assertFalse(
            _lacks_required_warmth("الباقات تظهر في الصفحة الرئيسية.", intent=INTENT_GENERAL)
        )

    def test_rewrite_pass_replaces_a_cold_model_answer(self):
        responses = [
            _FakeResponse("راجع الحقول المنبهة ثم أعد رفع الصورة بصيغة شائعة وتأكد من الاتصال."),
            _FakeResponse(
                "أتفهم أن هذا يعطّل شغلك. راجع الحقول المنبهة، ثم أعد رفع الصورة "
                "بصيغة شائعة وتأكد من استقرار الاتصال قبل المحاولة."
            ),
        ]

        with patch("reports.mansour_assistant.urlopen", side_effect=responses) as mocked:
            answer, _sources = ask_mansour(
                "الصورة ما ترضى تترفع والتقرير يرفض يحفظ",
                audience="teacher",
            )

        self.assertEqual(mocked.call_count, 2)
        self.assertIn("أتفهم", answer)
