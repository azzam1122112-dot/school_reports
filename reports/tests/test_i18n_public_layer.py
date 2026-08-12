# -*- coding: utf-8 -*-
"""الطبقة العامة تتكلّم لغتين، والداخلية تبقى عربية.

المنصة عربية المنشأ، وشاشات العمل الداخلية عربية بالتصميم لا بالإهمال: من
يستعملها موظفٌ في مدرسة سعودية. لكن الطبقة العامة — الهبوط والدخول والسياسات
والأسئلة — يقرؤها من ليس مستخدماً بعد: مستثمر، أو مدقّق، أو مدرسة دولية تقرأ
الشروط قبل التوقيع. فهذه وحدها تُترجَم.

وما يكسر ترجمةً كهذه ليس نصٌّ منسيّ، بل ثلاثة أعطال صامتة:

1. **قالبٌ يُثبّت ``dir="rtl"``** فتظهر الإنجليزية بمحاذاة معكوسة.
2. **``.po`` ينمو دون ترجمة** — يضيف ``makemessages`` مُدخلاً جديداً بعد كل
   تعديل قالب، فيبقى ``msgstr`` فارغاً ويُعرض النص العربي داخل صفحةٍ إنجليزية.
3. **CSS بخصائص فيزيائية** (``margin-left``) لا تنقلب مع الاتجاه.

الفحوص أدناه تحرس الثلاثة.
"""
from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

PO_PATH = Path(settings.BASE_DIR) / "locale" / "en" / "LC_MESSAGES" / "django.po"
MO_PATH = PO_PATH.with_suffix(".mo")


class LanguageConfigurationTests(SimpleTestCase):
    def test_arabic_is_the_default_language(self):
        self.assertEqual(settings.LANGUAGE_CODE, "ar")

    def test_only_arabic_and_english_are_offered(self):
        self.assertEqual([code for code, _ in settings.LANGUAGES], ["ar", "en"])

    def test_locale_middleware_sits_between_session_and_common(self):
        """ترتيبٌ ليس تفصيلاً: قبل الجلسة لا يقرأ الاختيار، وبعد Common يفوته
        إعادةُ التوجيه المعتمدة على اللغة."""
        order = settings.MIDDLEWARE
        session = order.index("django.contrib.sessions.middleware.SessionMiddleware")
        locale = order.index("django.middleware.locale.LocaleMiddleware")
        common = order.index("django.middleware.common.CommonMiddleware")
        self.assertLess(session, locale)
        self.assertLess(locale, common)

    def test_the_catalogue_is_compiled(self):
        self.assertTrue(MO_PATH.exists(), "لم يُنفَّذ compilemessages بعد آخر تعديل.")

    def test_the_language_cookie_is_hardened_in_production(self):
        """كوكيٌّ جديد ظهر مع المرحلة، فيلحق بتشديد بقيّة الكوكيز لا يُنسى.

        الفحص يقرأ المصدر لأن كتلة الإنتاج مشروطة بـ``ENV`` ولا تُنفَّذ في
        الاختبار — والمقروء هنا هو ما يصدر فعلاً على الخادم.
        """
        source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(
            encoding="utf-8"
        )
        production = source[source.index('if ENV == "production":') :]
        for flag in (
            "LANGUAGE_COOKIE_SECURE = True",
            "LANGUAGE_COOKIE_HTTPONLY = True",
            "LANGUAGE_COOKIE_SAMESITE",
        ):
            self.assertIn(flag, production)


class CatalogueCompletenessTests(SimpleTestCase):
    """كل ``msgid`` له ``msgstr``.

    هذا الفحص هو ما يكشف المُدخل الجديد الذي أضافه ``makemessages`` ولم يترجمه
    أحد — وهو العطل الوحيد الذي لا يظهر في أي صفحة تُفتح بالعربية.
    """

    @staticmethod
    def _entries() -> list[tuple[str, str]]:
        entries: list[tuple[str, str]] = []
        current_id: list[str] | None = None
        current_str: list[str] | None = None
        target: list[str] | None = None

        def flush() -> None:
            if current_id is not None and current_str is not None:
                entries.append(("".join(current_id), "".join(current_str)))

        for line in PO_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("msgid "):
                flush()
                current_id = [line[7:-1]]
                current_str = None
                target = current_id
            elif line.startswith("msgstr "):
                current_str = [line[8:-1]]
                target = current_str
            elif line.startswith('"') and target is not None:
                target.append(line[1:-1])
            elif not line.strip():
                target = None
        flush()
        return [(msgid, msgstr) for msgid, msgstr in entries if msgid]

    def test_every_message_has_an_english_translation(self):
        untranslated = [msgid for msgid, msgstr in self._entries() if not msgstr.strip()]
        self.assertEqual(
            untranslated,
            [],
            "مُدخلات بلا ترجمة — نفّذ makemessages ثم اكتب msgstr لكلٍّ منها:\n"
            + "\n".join(f"  - {msgid[:80]}" for msgid in untranslated[:20]),
        )

    def test_no_translation_merely_repeats_the_arabic_source(self):
        """نسخُ العربي في ``msgstr`` يُسكِت الفحص أعلاه دون أن يُترجم شيئاً."""
        arabic = re.compile(r"[؀-ۿ]")
        copied = [
            msgid
            for msgid, msgstr in self._entries()
            if msgstr.strip() and arabic.search(msgid) and arabic.search(msgstr)
        ]
        self.assertEqual(copied, [], f"ترجماتٌ بقيت عربية: {copied[:5]}")

    def test_the_catalogue_actually_covers_the_public_layer(self):
        """حارسُ نطاق: إن انكمش الفهرس فجأة فقد فُقد وسمُ ترجمة من قالب."""
        self.assertGreaterEqual(len(self._entries()), 400)


class LanguageSwitchingTests(TestCase):
    def test_the_landing_page_is_arabic_and_rtl_by_default(self):
        response = self.client.get(reverse("reports:landing"))
        html = response.content.decode("utf-8")
        self.assertIn('lang="ar"', html)
        self.assertIn('dir="rtl"', html)
        self.assertIn("تبدأ من صورة واضحة.", html)

    def _switch_to(self, code: str):
        return self.client.post(
            reverse("set_language"),
            {"language": code, "next": reverse("reports:landing")},
        )

    def test_switching_to_english_flips_both_the_language_and_the_direction(self):
        self._switch_to("en")
        html = self.client.get(reverse("reports:landing")).content.decode("utf-8")
        self.assertIn('lang="en"', html)
        self.assertIn('dir="ltr"', html)
        self.assertNotIn('dir="rtl"', html)
        self.assertIn("It starts with a clear picture.", html)

    def test_the_choice_survives_to_the_next_page(self):
        self._switch_to("en")
        html = self.client.get(reverse("reports:privacy_policy")).content.decode("utf-8")
        self.assertIn('lang="en"', html)
        self.assertIn("Controller and scope of this policy", html)

    def test_switching_back_to_arabic_restores_rtl(self):
        self._switch_to("en")
        self._switch_to("ar")
        html = self.client.get(reverse("reports:landing")).content.decode("utf-8")
        self.assertIn('lang="ar"', html)
        self.assertIn('dir="rtl"', html)

    def test_an_unoffered_language_is_ignored(self):
        """``set_language`` يتحقّق من ``LANGUAGES`` — والفحص يثبت أننا نعتمد عليه."""
        self._switch_to("fr")
        html = self.client.get(reverse("reports:landing")).content.decode("utf-8")
        self.assertIn('lang="ar"', html)

    def test_the_login_page_translates(self):
        self._switch_to("en")
        html = self.client.get(reverse("reports:login")).content.decode("utf-8")
        self.assertIn('lang="en"', html)
        self.assertIn("Passwordless sign-in", html)

    def test_the_faq_translates(self):
        self._switch_to("en")
        html = self.client.get(reverse("reports:faq")).content.decode("utf-8")
        self.assertIn("What is Tawtheeq?", html)


class LanguageSwitcherPlacementTests(TestCase):
    """المبدّل موجودٌ على كل مدخل عام — وإلا فالترجمة تعمل ولا يجدها أحد."""

    def test_the_switcher_is_reachable_from_every_public_entry_point(self):
        for route in (
            "reports:landing",
            "reports:login",
            "reports:privacy_policy",
            "reports:terms_conditions",
        ):
            with self.subTest(route=route):
                html = self.client.get(reverse(route)).content.decode("utf-8")
                self.assertIn('class="langswitch"', html)
                self.assertIn(reverse("set_language"), html)

    def test_the_switcher_posts_rather_than_links(self):
        """تغييرُ اللغة يغيّر حالة الجلسة، ولا يجوز أن ينفّذه مُسبِقُ تحميل."""
        html = self.client.get(reverse("reports:landing")).content.decode("utf-8")
        form = html[html.index('class="langswitch"') - 200 :][:400]
        self.assertIn('method="post"', form)
        self.assertIn("csrfmiddlewaretoken", form)


class DirectionAgnosticStylesheetTests(SimpleTestCase):
    """أوراقُ الطبقة العامة بخصائص منطقية لا فيزيائية.

    ``margin-left`` تبقى يساراً في الاتجاهين؛ و``margin-inline-start`` تنقلب.
    والفرق لا يظهر في العربية أبداً — يظهر فقط للقارئ الإنجليزي، أي لمن لا
    يُبلّغنا.
    """

    PUBLIC_SHEETS = ("landing.css", "legal.css", "utilities.css", "language-switcher.css")
    PHYSICAL = re.compile(
        r"(?:margin|padding|border)-(?:left|right)\s*:"
        r"|text-align\s*:\s*(?:left|right)\b"
        r"|(?<![\w-])(?:left|right)\s*:\s*(?!auto)",
    )
    # علامةٌ تجارية مرسومة بالـCSS: دائرتا «ماستركارد» ترتيبهما جزءٌ من العلامة
    # نفسها، فلو انقلبتا مع اتجاه الصفحة لصارت العلامة خطأً لا ترجمة.
    EXEMPT = (".payment-mark--mastercard",)

    def test_public_stylesheets_use_logical_properties(self):
        offenders: list[str] = []
        for name in self.PUBLIC_SHEETS:
            path = Path(settings.BASE_DIR) / "static" / "css" / name
            # التعليقات تشرح الخصائص الفيزيائية بالاسم، فتُفرَّغ لا تُحذف —
            # وإلا انزاحت أرقام الأسطر في الرسالة عن أرقام الملف.
            source = re.sub(
                r"/\*.*?\*/",
                lambda m: "\n" * m.group(0).count("\n"),
                path.read_text(encoding="utf-8"),
                flags=re.S,
            )
            for number, line in enumerate(source.splitlines(), start=1):
                if any(marker in line for marker in self.EXEMPT):
                    continue
                if self.PHYSICAL.search(line):
                    offenders.append(f"{name}:{number}: {line.strip()}")
        self.assertEqual(
            offenders,
            [],
            "خصائص فيزيائية لا تنقلب مع الاتجاه:\n" + "\n".join(offenders[:20]),
        )
