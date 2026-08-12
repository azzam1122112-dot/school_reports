# -*- coding: utf-8 -*-
"""يحرس أن التدهور يُعلَن — لا أن يُبتلع.

القيمة البديلة ليست موضع الاختبار وحدها: الكود القديم كان يُعيدها أيضاً. ما
يُختبر هنا هو ما كان مفقوداً — أن يبقى **أثر** يقول إن العملية تعثّرت.
"""
from __future__ import annotations

import logging

from django.test import SimpleTestCase

from core.observability import soft, soft_call, soft_fail


def _boom():
    raise RuntimeError("انفجار متعمَّد")


class SoftCallTests(SimpleTestCase):
    def test_returns_value_when_it_succeeds(self):
        self.assertEqual(soft_call("test.ok", lambda: 42, default=0), 42)

    def test_returns_default_and_logs_when_it_fails(self):
        with self.assertLogs("tawtheeq.degraded", level=logging.ERROR) as captured:
            value = soft_call("test.fallback", _boom, default=7)

        self.assertEqual(value, 7)
        self.assertIn("test.fallback", captured.output[0])
        # أثر الاستثناء نفسه يجب أن يصل، فهو ما يصل Sentry.
        self.assertIn("انفجار متعمَّد", captured.output[0])

    def test_context_reaches_the_log_line(self):
        with self.assertLogs("tawtheeq.degraded", level=logging.ERROR) as captured:
            soft_call("test.ctx", _boom, default=None, school_id=12, user_id=3)

        self.assertIn("school_id=12", captured.output[0])
        self.assertIn("user_id=3", captured.output[0])


class SoftFailTests(SimpleTestCase):
    def test_block_runs_and_outcome_is_ok(self):
        seen = []
        with soft_fail("test.block") as outcome:
            seen.append("ran")

        self.assertEqual(seen, ["ran"])
        self.assertTrue(outcome.ok)
        self.assertFalse(outcome.failed)

    def test_exception_is_suppressed_but_recorded(self):
        with self.assertLogs("tawtheeq.degraded", level=logging.ERROR):
            with soft_fail("test.block_fail", school_id=5) as outcome:
                _boom()

        self.assertTrue(outcome.failed)
        self.assertIsInstance(outcome.error, RuntimeError)
        self.assertEqual(outcome.error_type, "RuntimeError")

    def test_retained_outcome_is_picklable_and_holds_no_frames(self):
        """الأثر يُسجَّل ثم يُجرَّد.

        استثناءٌ يحتفظ بأثره يمسك إطاراته ومتغيّراتها المحلية، ويكسر أي مسار
        يُسلسِل الكائن — كاش بين العمليات، أو مُشغِّل اختبارات متوازٍ.
        """
        # الحمولة كائنٌ صنعه الاختبار نفسه، لا مدخلاً خارجياً.
        import pickle  # noqa: S403

        with self.assertLogs("tawtheeq.degraded", level=logging.ERROR):
            with soft_fail("test.picklable") as outcome:
                _boom()

        self.assertIsNone(outcome.error.__traceback__)
        restored = pickle.loads(pickle.dumps(outcome))  # noqa: S301
        self.assertTrue(restored.failed)
        self.assertEqual(restored.operation, "test.picklable")

    def test_caller_can_distinguish_empty_from_broken(self):
        """السبب الأصلي لوجود ``Outcome``: الشاشة تحتاج التفرقة."""
        with self.assertLogs("tawtheeq.degraded", level=logging.ERROR):
            with soft_fail("test.section") as outcome:
                _boom()

        context = {"items": [], "items_degraded": outcome.failed}
        self.assertTrue(context["items_degraded"])


class SoftDecoratorTests(SimpleTestCase):
    def test_decorator_preserves_success(self):
        @soft("test.dec", default=0)
        def add(a, b):
            return a + b

        self.assertEqual(add(2, 3), 5)

    def test_decorator_returns_default_and_logs(self):
        @soft("test.dec_fail", default=-1)
        def broken():
            _boom()

        with self.assertLogs("tawtheeq.degraded", level=logging.ERROR) as captured:
            self.assertEqual(broken(), -1)

        self.assertIn("test.dec_fail", captured.output[0])

    def test_decorator_keeps_function_identity(self):
        @soft("test.dec_name", default=None)
        def named_helper():
            """وثيقة."""

        self.assertEqual(named_helper.__name__, "named_helper")
        self.assertEqual(named_helper.__doc__, "وثيقة.")


class DegradationCounterTests(SimpleTestCase):
    def test_failure_increments_the_operational_counter(self):
        from core import opmetrics

        before = opmetrics.read_current("degraded:_total") or 0
        with self.assertLogs("tawtheeq.degraded", level=logging.ERROR):
            soft_call("test.counter", _boom, default=None)
        after = opmetrics.read_current("degraded:_total") or 0

        self.assertGreater(after, before)
