# -*- coding: utf-8 -*-
"""لا طرفَ ثالثاً في مسار العرض، ولا نطاقَ خارجياً في سياسة المحتوى.

كانت أربعة عشر قالباً تفتح اتصالاً بـ``fonts.googleapis.com`` ثم
``fonts.gstatic.com`` قبل رسم أول حرف، وبعضها يُحمّل Font Awesome من
``cdnjs.cloudflare.com`` بينما الحزمة مُستضافة محلياً أصلاً منذ زمن.

والثمن كان ثلاثياً: أربع رحلات شبكة قبل أول حرف على شبكة مدرسةٍ قد تكون بطيئة،
وتسريبُ عنوان كل زائر لطرف ثالث في كل صفحة، **وإلزامُ سياسة المحتوى بالسماح
لنطاقات خارجية في ``style-src`` و``font-src``** — أي ثغرةٌ في السياسة لأجل خطّ.

هذه الفحوص تمنع عودة أيٍّ من ذلك.
"""
from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

THIRD_PARTY_HOSTS = (
    "fonts.googleapis.com",
    "fonts.gstatic.com",
    "cdnjs.cloudflare.com",
    "cdn.jsdelivr.net",
    "unpkg.com",
)


class SelfHostedFontAssetTests(SimpleTestCase):
    @property
    def root(self) -> Path:
        return Path(settings.BASE_DIR)

    def test_no_template_reaches_a_third_party_for_styles_or_fonts(self):
        offenders: list[str] = []
        for path in (self.root / "reports" / "templates").rglob("*.html"):
            source = path.read_text(encoding="utf-8")
            for host in THIRD_PARTY_HOSTS:
                if host in source:
                    offenders.append(f"{path.relative_to(self.root)} → {host}")
        self.assertEqual(offenders, [], "قوالب ما زالت تطلب من طرف ثالث")

    def test_the_font_files_are_vendored(self):
        font_dir = self.root / "static" / "vendor" / "fonts" / "cairo"
        files = sorted(p.name for p in font_dir.glob("*.woff2"))
        self.assertEqual(
            files,
            ["cairo-arabic.woff2", "cairo-latin-ext.woff2", "cairo-latin.woff2"],
        )

    def test_the_font_sheet_declares_one_variable_face_per_subset(self):
        """ملفٌ واحد لكل مجموعة محارف يغطّي كل الأوزان.

        غوغل تُرسل الملفَ نفسه لكل وزنٍ يُطلب، فتنزيلُ ستة أوزان كان ستّ نسخ
        من ملفٍ واحد. والاختبار يقفل التوفير حتى لا يعود أحد فيضيف وجهاً لكل وزن.
        """
        css = (self.root / "static" / "css" / "fonts.css").read_text(encoding="utf-8")

        faces = re.findall(r"@font-face\s*\{", css)
        self.assertEqual(len(faces), 3, "يُتوقَّع وجهٌ واحد لكل مجموعة محارف")
        self.assertEqual(css.count("font-weight: 200 1000;"), 3)
        self.assertEqual(css.count("font-display: swap;"), 3)
        # التقسيم يبقى: صفحةٌ عربية بحتة لا تُنزّل اللاتيني.
        self.assertEqual(css.count("unicode-range:"), 3)

        # الفحص على ما **يُحمَّل** لا على ما يُذكر: التعليق أعلى الملف يشرح لماذا
        # أُزيلت هذه النطاقات، فذكرُ الاسم فيه ليس طلباً لطرف ثالث.
        loaded = re.findall(r"url\(\s*['\"]?([^'\")]+)", css)
        self.assertEqual(len(loaded), 3)
        for url in loaded:
            self.assertTrue(
                url.startswith("../vendor/fonts/cairo/"),
                f"مصدر خطٍّ غير محلي: {url}",
            )

    def test_font_payload_stays_small(self):
        """سقفٌ على وزن الخطوط — 79KB اليوم، والحدّ 120KB.

        الرقم ليس تجميلاً: هذه بايتات تُنزَّل قبل أول حرف على شبكة قد تكون
        بطيئة. وتجاوزُ الحدّ يجب أن يكون قراراً مكتوباً لا انزلاقاً.
        """
        font_dir = self.root / "static" / "vendor" / "fonts" / "cairo"
        total = sum(p.stat().st_size for p in font_dir.glob("*.woff2"))
        self.assertLess(total, 120 * 1024, f"وزن الخطوط {total / 1024:.0f}KB")


@override_settings(ALLOWED_HOSTS=["testserver"], CSP_ENABLED=True, CSP_REPORT_ONLY=False)
class ContentSecurityPolicyHostTests(TestCase):
    def _policy(self, url: str) -> str:
        response = self.client.get(url)
        return response.headers.get("Content-Security-Policy", "")

    def test_policy_allows_no_external_style_or_font_host(self):
        policy = self._policy(reverse("reports:login"))

        self.assertIn("style-src", policy)
        for host in THIRD_PARTY_HOSTS:
            self.assertNotIn(host, policy, f"{host} ما زال مسموحاً في السياسة")

    def test_style_and_font_sources_are_self_only(self):
        policy = self._policy(reverse("reports:login"))

        self.assertIn("font-src 'self' data:", policy)

    def test_inline_styles_are_no_longer_permitted(self):
        """``style-src`` أُغلقت — وهذا هو حاصل المرحلة كلها.

        الإذن كان مفتوحاً لأن القوالب حملت 806 سمة ``style``. أُزيلت كلها،
        وصار كل ``<style>`` يحمل ``nonce``. فالسياسة الآن تسمح بورقة الأنماط
        المملوكة وبالكتل المُوقَّعة، ولا تسمح بشيء يأتي من محتوى الصفحة.
        """
        policy = self._policy(reverse("reports:login"))

        style_directives = [
            part.strip()
            for part in policy.split(";")
            if part.strip().startswith("style-src")
        ]
        self.assertTrue(style_directives, policy)
        for directive in style_directives:
            self.assertNotIn("'unsafe-inline'", directive, directive)

        self.assertIn("style-src-attr 'none'", policy)
        self.assertRegex(policy, r"style-src 'self' 'nonce-[^']+'")
