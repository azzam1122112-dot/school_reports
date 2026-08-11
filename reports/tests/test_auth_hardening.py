# reports/tests/test_auth_hardening.py
# -*- coding: utf-8 -*-
"""حرّاس انحدار لمسار تسجيل الدخول — SEC-002 و SEC-003.

هذان الاختباران يحرسان خاصيتين تنكسران **بصمت** إن انكسرتا: الأولى تختفي
حمايتها دون رسالة خطأ، والثانية تسرّب بيانات دون أن يلاحظها أحد. ولذلك
يُختبران بالسلوك لا بقراءة الكود.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import Client, TestCase, override_settings
from django.urls import reverse

from reports.models import Teacher


def _broken_store():
    """مخزن حدود ساقط — كل عملية عليه ترمي كما يفعل Redis غير المتاح."""
    store = MagicMock()
    store.get.side_effect = ConnectionError("redis unavailable")
    store.add.side_effect = ConnectionError("redis unavailable")
    store.incr.side_effect = ConnectionError("redis unavailable")
    store.delete.side_effect = ConnectionError("redis unavailable")
    return store


class LoginThrottleFailClosedTests(TestCase):
    """SEC-002 — تعذّر قراءة عدّاد المحاولات يجب ألا يفتح باب التخمين."""

    @override_settings(LOGIN_THROTTLE_FAIL_CLOSED=True)
    def test_login_is_refused_when_the_throttle_store_is_unavailable(self):
        # الإعداد مثبَّت هنا لا متروك للافتراض: الافتراض مشتقّ من وجود
        # ``REDIS_LIMITS_URL``، وهو غائب في بيئة الاختبار — وهذا الاختبار يخصّ
        # سلوك الفشل المغلق ذاته، لا شرط تفعيله.
        with patch("reports.views.auth.limits_cache", return_value=_broken_store()):
            response = Client().post(
                reverse("reports:login"),
                {"phone": "0500000000", "password": "irrelevant"},
                follow=False,
            )

        # يُعاد التوجيه إلى صفحة الدخول برسالة التهدئة — لا يُمرَّر الطلب.
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse("reports:login"), response["Location"])

    def test_fail_closed_behaviour_is_configurable(self):
        """التشغيل قد يحتاج فتح الباب مؤقتاً أثناء حادثة — والافتراض مغلق."""
        from reports.views.auth import _login_account_locked

        with patch("reports.views.auth.limits_cache", return_value=_broken_store()):
            with override_settings(LOGIN_THROTTLE_FAIL_CLOSED=True):
                self.assertTrue(_login_account_locked("0500000000"))
            with override_settings(LOGIN_THROTTLE_FAIL_CLOSED=False):
                self.assertFalse(_login_account_locked("0500000000"))

    def test_a_write_failure_alone_does_not_block_login(self):
        """القراءة هي الحارس وحدها.

        تسجيلُ الإخفاق عملية جانبية: فشلُها لا يبرّر إسقاط الطلب، وإلا صار
        تعثّرٌ لحظي في المخزن يقفل الدخول لمن كتب كلمة مروره صحيحة.
        """
        from reports.views.auth import _register_login_failure

        with patch("reports.views.auth.limits_cache", return_value=_broken_store()):
            _register_login_failure("0500000000")  # لا يرمي إلى الأعلى

        # اعتماد خاطئ بمخزن سليم ⇒ تُعاد صفحة الدخول (200)، لا إعادة توجيه
        # التهدئة (302) التي يسلكها مسار الإقفال.
        response = Client().post(
            reverse("reports:login"),
            {"phone": "0500000000", "password": "irrelevant"},
        )
        self.assertEqual(response.status_code, 200)

    def test_the_setting_exists_and_is_boolean(self):
        """الإعداد يُقرأ في مسار الدخول — فغيابه يعني اعتماداً على قيمة ضمنية."""
        from django.conf import settings

        self.assertIsInstance(
            getattr(settings, "LOGIN_THROTTLE_FAIL_CLOSED", None),
            bool,
            "LOGIN_THROTTLE_FAIL_CLOSED غير معرَّف — والسلوك صار رهن قيمة ضمنية",
        )


class LoginPiiLoggingTests(TestCase):
    """SEC-003 — رقم الجوال والهوية بيانات شخصية، ولا مكان لها في السجلّات."""

    NATIONAL_ID = "1098765432"
    PHONE = "0555123456"

    @classmethod
    def setUpTestData(cls):
        cls.inactive_user = Teacher.objects.create_user(
            phone=cls.PHONE, password="Str0ng!Passw0rd", name="حساب موقوف"
        )
        cls.inactive_user.is_active = False
        cls.inactive_user.save(update_fields=["is_active"])

    def _post_login(self, identifier: str, password: str = "wrong-password"):
        return Client().post(
            reverse("reports:login"), {"phone": identifier, "password": password}
        )

    def test_invalid_credentials_do_not_log_the_raw_identifier(self):
        with self.assertLogs("reports.views.auth", level="WARNING") as captured:
            self._post_login(self.NATIONAL_ID)
        joined = "\n".join(captured.output)
        self.assertNotIn(self.NATIONAL_ID, joined)
        self.assertIn("identifier_hash=", joined)

    def test_inactive_account_path_does_not_log_the_raw_identifier(self):
        with self.assertLogs("reports.views.auth", level="WARNING") as captured:
            self._post_login(self.PHONE, password="Str0ng!Passw0rd")
        joined = "\n".join(captured.output)
        self.assertNotIn(self.PHONE, joined)
        self.assertIn("identifier_hash=", joined)

    def test_account_throttle_path_does_not_log_the_raw_identifier(self):
        from reports.views.auth import LOGIN_ACCOUNT_MAX_FAILURES, _register_login_failure

        for _ in range(LOGIN_ACCOUNT_MAX_FAILURES + 1):
            _register_login_failure(self.NATIONAL_ID)

        with self.assertLogs("reports.views.auth", level="WARNING") as captured:
            self._post_login(self.NATIONAL_ID)
        joined = "\n".join(captured.output)
        self.assertNotIn(self.NATIONAL_ID, joined)
        self.assertIn("identifier_hash=", joined)

    def test_the_hash_is_stable_and_not_reversible_to_the_identifier(self):
        from reports.views.auth import _identifier_for_log

        first = _identifier_for_log(self.NATIONAL_ID)
        second = _identifier_for_log(self.NATIONAL_ID)
        self.assertEqual(first, second, "التجزئة يجب أن تربط محاولات المهاجم الواحد")
        self.assertNotIn(self.NATIONAL_ID, first)
        self.assertNotEqual(first, _identifier_for_log("1098765433"))
        self.assertEqual(_identifier_for_log(""), "-")
