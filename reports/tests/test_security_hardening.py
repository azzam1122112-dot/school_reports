# reports/tests/test_security_hardening.py
# -*- coding: utf-8 -*-
"""حرّاس التصلّب الأمني — SEC-004 و SEC-005 و SEC-007 و SEC-009 وفصل مخزن الحدود.

كل خاصية هنا يُعاد ضبطها بسطر إعداد واحد. والسطر يُعاد بحسن نية عادةً — لتشخيص
عطل، أو لتسريع تطوير — ثم يُنسى. فالحارس ليس تجاه من يريد إضعاف النظام، بل
تجاه من ينسى أن يعيد ما استعاره.
"""
from __future__ import annotations

from django.conf import settings
from django.test import Client, SimpleTestCase, TestCase, override_settings
from django.urls import reverse


class SignedMediaUrlLifetimeTests(SimpleTestCase):
    """SEC-005 — الرابط الموقَّع يحمل التخويل بذاته، فعمره هو نافذة التسريب."""

    def test_signed_media_urls_expire_within_one_hour(self):
        # يُقرأ الثابت لا ``AWS_QUERYSTRING_EXPIRE``: الأخير لا يُعرَّف إلا حين
        # يكون R2 مهيّأً، فاختبارُه في بيئة بلا R2 يفحص قيمةً افتراضية لا وجود
        # لها في الإنتاج — أي يمرّ دائماً ولا يحرس شيئاً.
        expiry = getattr(settings, "MEDIA_SIGNED_URL_EXPIRE_SECONDS", None)
        self.assertIsNotNone(expiry, "سياسة عمر الروابط الموقَّعة غير معرَّفة")
        self.assertLessEqual(
            int(expiry),
            3600,
            "روابط الوسائط الموقَّعة تعيش أكثر من ساعة — أي أن رابطاً مسرَّباً "
            "يفتح ملف مدرسة لمن ليس عضواً فيها طوال تلك المدة",
        )

    def test_the_storage_backend_actually_uses_that_policy(self):
        """ثابتٌ لا يقرؤه أحد ليس سياسة. الربط نفسه هو ما يُحرَس هنا."""
        import re
        import pathlib

        source = (pathlib.Path(settings.BASE_DIR) / "config" / "settings.py").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            source,
            re.compile(
                r"^\s*AWS_QUERYSTRING_EXPIRE\s*=\s*MEDIA_SIGNED_URL_EXPIRE_SECONDS\s*$",
                re.M,
            ),
            "AWS_QUERYSTRING_EXPIRE لم يعد مشتقاً من سياسة العمر الموحّدة",
        )

    def test_public_media_access_stays_disabled(self):
        self.assertFalse(getattr(settings, "MEDIA_PUBLIC_ACCESS_ENABLED", False))


class LimitsStoreIsolationTests(SimpleTestCase):
    """مخزن العدّادات يجب أن يبقى قابلاً للفصل، وأن يعمل بلا فصل."""

    def test_the_helper_always_returns_a_usable_store(self):
        from core.limits_cache import limits_cache

        store = limits_cache()
        store.set("limits-selftest", 1, 5)
        self.assertEqual(store.get("limits-selftest"), 1)
        store.delete("limits-selftest")

    def test_isolation_is_reported_honestly(self):
        """فحص ما قبل الإنتاج يقرأ هذه الدالة — فكذبها أسوأ من غياب الفصل."""
        from core.limits_cache import limits_cache_is_isolated

        # لا مخزن ``limits`` في إعدادات الاختبار ⇒ لا فصل، ويجب أن يُقال ذلك.
        self.assertFalse(limits_cache_is_isolated())

    def test_fail_closed_tracks_whether_a_dedicated_store_exists(self):
        """الفشل المغلق يفترض مخزناً موثوقاً — فلا يُشغَّل قبل وجوده.

        بلا مخزن مستقل يبقى السلوك مفتوحاً (وحدُّ الـ IP قائم)، وبإضافة
        ``REDIS_LIMITS_URL`` يشتدّ تلقائياً. الربط يمنع الحالة الوحيدة الخطرة:
        فشلٌ مغلق على كاش قابل للإخلاء، حيث يقفل إخلاءُ مفتاحٍ المنصةَ كلها.
        """
        from core.limits_cache import limits_cache_is_isolated

        fail_closed = bool(getattr(settings, "LOGIN_THROTTLE_FAIL_CLOSED", False))
        if fail_closed:
            self.assertTrue(
                limits_cache_is_isolated(),
                "الفشل المغلق مُفعَّل بلا مخزن حدود مستقل — إخلاءُ مفتاحٍ عادي "
                "يقفل الدخول للجميع",
            )

    def test_the_default_is_derived_from_the_limits_store_not_hardcoded(self):
        import pathlib
        import re

        source = (pathlib.Path(settings.BASE_DIR) / "config" / "settings.py").read_text(
            encoding="utf-8"
        )
        self.assertRegex(
            source,
            re.compile(
                r"LOGIN_THROTTLE_FAIL_CLOSED\s*=\s*_env_bool\(\s*"
                r"[\"']LOGIN_THROTTLE_FAIL_CLOSED[\"']\s*,\s*bool\(REDIS_LIMITS_URL\)"
            ),
            "الافتراض عاد ثابتاً — وهو يعيد فخّ ترتيب الخطوتين في النشر",
        )


@override_settings(ENV="production", CSP_ENABLED=True, CSP_REPORT_ONLY=False)
class ContentSecurityPolicyTests(TestCase):
    """SEC-007 — ``script-src`` يحصر السكربتات في ما تملكه المنصة."""

    def _policy(self) -> str:
        response = self.client.get(reverse("reports:landing"), secure=True)
        self.assertEqual(response.status_code, 200)
        return response.headers.get("Content-Security-Policy", "")

    def test_no_public_cdn_is_an_allowed_script_source(self):
        policy = self._policy()
        for cdn in ("cdn.jsdelivr.net", "unpkg.com", "cdnjs.cloudflare.com/ajax/libs"):
            self.assertNotIn(
                cdn,
                policy.split("style-src")[0],
                f"{cdn} أصلٌ مسموح في script-src — وهو يخدم كل حزمة منشورة عليه",
            )

    def test_the_structural_directives_stay_locked(self):
        policy = self._policy()
        self.assertIn("frame-ancestors 'none'", policy)
        self.assertIn("object-src 'none'", policy)
        self.assertIn("base-uri 'self'", policy)
        self.assertIn("default-src 'self'", policy)

    def test_scripts_are_nonce_gated(self):
        policy = self._policy()
        self.assertIn("'nonce-", policy)
        self.assertNotIn("script-src 'self' 'unsafe-inline'", policy)
        self.assertNotIn("'unsafe-eval'", policy)


class ChartJsSelfHostingTests(SimpleTestCase):
    """الأصل المُستضاف يجب أن يكون موجوداً فعلاً — وإلا فاللوحات بلا رسوم."""

    def test_the_vendored_chart_bundle_is_present(self):
        from django.contrib.staticfiles import finders

        located = finders.find("js/vendor/chart.umd.min.js")
        self.assertIsNotNone(
            located, "Chart.js المُستضاف مفقود بعد إزالة الـ CDN من CSP"
        )

    def test_no_template_still_loads_scripts_from_a_public_cdn(self):
        import pathlib
        import re

        root = pathlib.Path(settings.BASE_DIR) / "reports" / "templates"
        pattern = re.compile(r"<script[^>]+src=[\"']https?://(?!eauthenticate\.)", re.I)
        offenders = [
            str(path.relative_to(root))
            for path in root.rglob("*.html")
            if pattern.search(path.read_text(encoding="utf-8", errors="ignore"))
        ]
        self.assertEqual(
            offenders, [], f"قوالب ما زالت تحمّل سكربتات من نطاق خارجي: {offenders}"
        )


class MansourCrossSiteTests(TestCase):
    """SEC-009 — نقطة معفاة من CSRF وتكلف مالاً: تُقبل من أصلها وحده."""

    def _post(self, **headers):
        return Client().post(
            reverse("reports:mansour_assistant_reply"),
            data="{}",
            content_type="application/json",
            **headers,
        )

    def test_a_cross_site_request_is_refused(self):
        response = self._post(HTTP_SEC_FETCH_SITE="cross-site")
        self.assertEqual(response.status_code, 403)

    def test_same_origin_requests_are_not_refused_by_this_guard(self):
        response = self._post(HTTP_SEC_FETCH_SITE="same-origin")
        self.assertNotEqual(response.status_code, 403)

    def test_a_browser_that_omits_the_header_is_not_locked_out(self):
        """رفض من لا يرسل الترويسة يقطع الخدمة عن متصفحات قديمة بلا مقابل."""
        response = self._post()
        self.assertNotEqual(response.status_code, 403)


class AuditRetentionTests(SimpleTestCase):
    """SEC-010 — نافذة التحقيق لا تُقاس بأسابيع."""

    def test_audit_logs_are_kept_long_enough_to_investigate_an_incident(self):
        self.assertGreaterEqual(
            int(getattr(settings, "AUDIT_LOG_RETENTION_DAYS", 30)),
            180,
            "احتفاظ سجل التدقيق أقصر من دورة اكتشاف حادثة أمنية أو نزاع تجاري",
        )
