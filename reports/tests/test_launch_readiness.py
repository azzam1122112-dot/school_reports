# reports/tests/test_launch_readiness.py
# -*- coding: utf-8 -*-
"""حرّاس الجاهزية للإطلاق — الفحوص التي تمنع نشراً ناقصاً.

الفرق بين هذا الملف و``test_security_hardening.py``: ذاك يحرس **الإعداد**، وهذا
يحرس **الآلية التي تكتشف الإعداد الخاطئ**. فحصٌ معطّل لا يُلاحَظ: يمرّ النشر
أخضر وهو لا يفحص شيئاً، وهو أسوأ من غياب الفحص لأنه يمنح ثقة كاذبة.
"""
from __future__ import annotations

from django.test import SimpleTestCase


class CompromisedSecretDetectionTests(SimpleTestCase):
    """SEC-001 — البصمات المسجَّلة يجب أن تظل قادرة على الكشف."""

    # القيمة المسرَّبة نفسها لا تُكتب هنا. هذه بصمة أحد مفاتيح التوقيع التي
    # ظهرت في ``git log --all -- .env`` — تكفي لإثبات أن الآلية تعمل.
    KNOWN_LEAKED_SECRET_KEY_SHA256 = (
        "cf6898176dc6f7c02036ddcf54c55153a646387a571b8aedb94ba2933a5be92f"
    )

    def test_the_fingerprint_list_is_not_empty(self):
        """قائمةٌ أُفرغت تجعل الفحص يمرّ دائماً بلا أن يفحص."""
        from core.compromised_secrets import _COMPROMISED, compromised_names

        self.assertIn("SECRET_KEY", compromised_names())
        for name in compromised_names():
            self.assertTrue(
                _COMPROMISED[name],
                f"بصمات {name} أُفرغت — الفحص صار يمرّ بلا أن يفحص",
            )

    def test_a_known_leaked_key_is_still_recognised(self):
        from core.compromised_secrets import _COMPROMISED

        self.assertIn(
            self.KNOWN_LEAKED_SECRET_KEY_SHA256,
            _COMPROMISED["SECRET_KEY"],
            "بصمة مفتاح مسرَّب حُذفت من القائمة — والحذف منها يفتح باباً أُغلق",
        )

    def test_a_fresh_secret_is_not_flagged(self):
        """إنذار كاذب يعطّل النشر ويُدرَّب الفريق على تجاوز الفحص."""
        import secrets as _secrets

        from core.compromised_secrets import is_compromised

        for _ in range(5):
            self.assertFalse(is_compromised("SECRET_KEY", _secrets.token_urlsafe(64)))

    def test_empty_and_missing_values_are_not_flagged(self):
        from core.compromised_secrets import is_compromised

        self.assertFalse(is_compromised("SECRET_KEY", ""))
        self.assertFalse(is_compromised("SECRET_KEY", None))
        self.assertFalse(is_compromised("NOT_A_TRACKED_NAME", "anything"))

    def test_the_fingerprint_is_not_reversible(self):
        from core.compromised_secrets import fingerprint

        digest = fingerprint("some-secret-value")
        self.assertEqual(len(digest), 64)
        self.assertNotIn("some-secret-value", digest)
        self.assertEqual(digest, fingerprint("  some-secret-value  "))


class SentryScrubberTests(SimpleTestCase):
    """البيانات الشخصية لا تغادر الخادم — وما غادر لا يُستعاد."""

    def _scrub(self, event):
        from config.settings import _sentry_scrub

        return _sentry_scrub(event, {})

    def test_sensitive_request_fields_are_filtered(self):
        event = self._scrub(
            {
                "request": {
                    "data": {
                        "password": "hunter2",
                        "new_password1": "hunter2",
                        "phone": "0555123456",
                        "national_id": "1098765432",
                        "title": "تقرير أسبوعي",
                    },
                    "headers": {"Authorization": "Bearer abc", "Accept": "text/html"},
                    "cookies": {"sessionid": "abc123"},
                    "query_string": "token=abc&q=x",
                }
            }
        )
        data = event["request"]["data"]
        for field in ("password", "new_password1", "phone", "national_id"):
            self.assertEqual(data[field], "[Filtered]", f"{field} لم يُنقَّ")
        # ما ليس حسّاساً يبقى، وإلا صار التقرير بلا قيمة تشخيصية.
        self.assertEqual(data["title"], "تقرير أسبوعي")
        self.assertEqual(event["request"]["headers"]["Authorization"], "[Filtered]")
        self.assertEqual(event["request"]["headers"]["Accept"], "text/html")
        self.assertNotIn("cookies", event["request"])
        self.assertEqual(event["request"]["query_string"], "[Filtered]")

    def test_local_frame_variables_are_filtered(self):
        """أخطر مصدر: متغيّرات الإطار تحمل ما مرّ بالدالة كاملاً."""
        event = self._scrub(
            {
                "exception": {
                    "values": [
                        {
                            "stacktrace": {
                                "frames": [
                                    {
                                        "vars": {
                                            "password": "hunter2",
                                            "identifier": "1098765432",
                                            "school_id": 12,
                                        }
                                    }
                                ]
                            }
                        }
                    ]
                }
            }
        )
        variables = event["exception"]["values"][0]["stacktrace"]["frames"][0]["vars"]
        self.assertEqual(variables["password"], "[Filtered]")
        # ``identifier`` تحديداً: هو اسم المتغيّر الحامل لرقم الجوال/الهوية في
        # ``login_view``، ومسار الفشل هناك هو أكثر ما يرمي في المنصة.
        self.assertEqual(variables["identifier"], "[Filtered]")
        self.assertEqual(variables["school_id"], 12)

    def test_nested_structures_are_reached(self):
        event = self._scrub(
            {"extra": {"payload": {"user": {"phone": "0555123456", "name": "أحمد"}}}}
        )
        user = event["extra"]["payload"]["user"]
        self.assertEqual(user["phone"], "[Filtered]")
        self.assertEqual(user["name"], "أحمد")

    def test_a_malformed_event_is_dropped_not_leaked(self):
        """إن تعطّلت التنقية فالإسقاط أأمن من الإرسال غير المنقَّى."""

        class Hostile(dict):
            def get(self, *args, **kwargs):
                raise RuntimeError("boom")

        self.assertIsNone(self._scrub(Hostile()))

    def test_the_scrubber_is_wired_into_sentry_init(self):
        """دالةُ تنقيةٍ لا تُمرَّر إلى ``init`` ليست تنقية."""
        import pathlib
        import re

        from django.conf import settings

        source = (pathlib.Path(settings.BASE_DIR) / "config" / "settings.py").read_text(
            encoding="utf-8"
        )
        self.assertRegex(source, re.compile(r"before_send\s*=\s*_sentry_scrub"))
        self.assertRegex(source, re.compile(r"send_default_pii\s*=\s*False"))


class PreflightCoverageTests(SimpleTestCase):
    """فحص ما قبل النشر يُشغَّل بعد كل إصدار — فما لا يدخله لا يُتحقَّق منه."""

    def test_the_new_checks_are_registered_in_the_run_order(self):
        from reports.management.commands.production_preflight import Command

        command = Command()
        for name in ("_check_abuse_limits", "_check_data_exposure", "_check_compromised_secrets"):
            self.assertTrue(
                hasattr(command, name), f"فحص {name} مفقود من production_preflight"
            )

        import inspect

        source = inspect.getsource(Command.handle)
        for name in ("_check_abuse_limits", "_check_data_exposure"):
            self.assertIn(
                name, source, f"فحص {name} معرَّف لكنه غير مُستدعى — أي لا يعمل"
            )
