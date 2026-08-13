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

import json
import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
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

    def test_no_entry_is_left_marked_fuzzy(self):
        """المُدخل «المشكوك فيه» يُترجَم عربياً في صفحةٍ إنجليزية بلا أي إنذار.

        حين يضيف ``makemessages`` نصاً جديداً يبحث ``msgmerge`` عن أقرب نصٍّ
        قديم ويضع ترجمته مكانه موسومةً ``#, fuzzy`` — أي «خمّنتُ، فراجِعْ».
        و``msgfmt`` يتجاهل كل موسومٍ بذلك، فلا يدخل ``.mo`` أصلاً: العنوان
        يبقى عربياً في صفحةٍ ``lang="en"``. والفحصان أعلاه لا يريانه، لأن
        ``msgstr`` ليس فارغاً وليس عربياً — بل هو ترجمةُ نصٍّ آخر تماماً.

        لذلك: كل تخمين يُراجَع ويُحذف وسمُه، أو يُعاد كتابته.
        """
        fuzzy: list[str] = []
        marked = False
        for line in PO_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("#,") and "fuzzy" in line:
                marked = True
            elif line.startswith("msgid ") and marked:
                fuzzy.append(line[7:-1])
                marked = False
            elif not line.strip():
                marked = False
        self.assertEqual(
            fuzzy,
            [],
            "تخميناتٌ لم تُراجَع — ``msgfmt`` يسقطها فتظهر عربية بالإنجليزية:\n"
            + "\n".join(f"  - {msgid[:80]}" for msgid in fuzzy[:20]),
        )


class LanguageSwitchingTests(TestCase):
    def test_the_landing_page_is_arabic_and_rtl_by_default(self):
        response = self.client.get(reverse("reports:landing"))
        html = response.content.decode("utf-8")
        self.assertIn('lang="ar"', html)
        self.assertIn('dir="rtl"', html)
        self.assertIn("منصة واحدة لإدارة تقارير المدرسة وطلباتها وتعاميمها", html)

    def test_a_new_visitor_with_english_browser_still_gets_the_arabic_landing(self):
        html = self.client.get(
            reverse("reports:landing"),
            HTTP_ACCEPT_LANGUAGE="en-US,en;q=0.9,ar;q=0.2",
        ).content.decode("utf-8")

        self.assertIn('lang="ar"', html)
        self.assertIn("منصة واحدة لإدارة تقارير المدرسة وطلباتها وتعاميمها", html)
        self.assertNotIn("One platform for school reports", html)

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
        self.assertIn("One platform for school reports, requests and circulars", html)

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

    def test_no_public_page_is_left_half_arabic(self):
        """الفحص الحاسم: صفحةٌ إنجليزية لا تحمل نصاً عربياً مرئياً.

        الترجمة الجزئية أسوأ من غيابها: القارئ يظنّ الصفحة مترجمة ثم يصطدم
        بقائمة تنقّلٍ أو زرٍّ أو سؤالٍ شائعٍ عربي. فيُقرأ الجسدُ كلُّه لا عيّنةٌ
        منه، وتُستثنى ثلاثة مواضع عربيتُها صحيحة:

        * اسم اللغة داخل مبدّل اللغة — يُكتب دائماً بلغته.
        * بيانات السجل التجاري — اسمٌ قانوني مقيَّد لا يُترجَم.
        * تعليقات الجافاسكربت — لا يقرؤها زائر.
        """
        arabic = re.compile(r"[؀-ۿ]")
        # قيمُ السجل التجاري تأتي من الإعدادات وتُغرَس داخل جملٍ مترجَمة، فتُزال
        # من النص قبل الفحص بدل استثناء السطر كلّه ومعه ترجمتُه.
        registered = [
            str(getattr(settings, name, "") or "").strip()
            for name in (
                "BUSINESS_LEGAL_NAME",
                "BUSINESS_ADDRESS",
                "BUSINESS_FREELANCE_ACTIVITY",
                "BUSINESS_LICENSES",
            )
        ]
        registered = [value for value in registered if arabic.search(value)]
        # واسمُ بوّابة الدفع المفعّلة كذلك: علامةٌ تجارية مسجّلة بالعربية.
        payment_gateways = "ميسر"
        self._switch_to("en")

        for route in (
            "reports:landing",
            "reports:login",
            "reports:register_school",
            "reports:faq",
            "reports:privacy_policy",
            "reports:terms_conditions",
            "reports:refund_policy",
            "reports:complaints_policy",
            "reports:service_delivery_policy",
        ):
            with self.subTest(route=route):
                html = self.client.get(reverse(route)).content.decode("utf-8")
                body = re.sub(r"<style\b.*?</style>", "", html, flags=re.S | re.I)
                body = re.sub(r"<!--.*?-->", "", body, flags=re.S)
                body = re.sub(r"/\*.*?\*/", "", body, flags=re.S)
                # تعليقات الجافاسكربت، في أول السطر أو في ذيله. و``(?<!:)``
                # تحمي ``https://`` من أن يُقرأ بدايةَ تعليق.
                body = re.sub(r"(?<!:)//.*$", "", body, flags=re.M)
                for value in registered:
                    body = body.replace(value, "")
                if payment_gateways:
                    body = body.replace(payment_gateways, "")

                offenders = [
                    line.strip()
                    for line in body.splitlines()
                    if arabic.search(line)
                    and "langswitch" not in line
                    and "Switch the site language" not in line
                    and "<span>العربية</span>" not in line
                    and "<dd>" not in line
                    and "legalName" not in line
                    and "alternateName" not in line
                    # مطابقةُ كلماتٍ مفتاحية داخل تعبير نمطي، لا نصٌّ معروض.
                    and "test(s)" not in line
                    # عناوينُ حوارٍ لا يفتحه إلا إجراءٌ داخلي على شاشةٍ داخلية.
                    and "rcPrompt" not in line
                    and not line.strip().startswith(("title:", "okText:"))
                ]
                self.assertEqual(
                    offenders,
                    [],
                    f"نصٌّ عربي في صفحةٍ إنجليزية ({route}):\n"
                    + "\n".join(f"  {line[:120]}" for line in offenders[:12]),
                )


class InstallableAppSpeaksBothLanguagesTests(TestCase):
    """التطبيق المثبَّت يحمل لغته معه.

    ثلاثةُ نصوصٍ في الـPWA لا يبلغها ``{% translate %}`` بطبيعتها، وكلٌّ منها
    عولج بطريقته:

    * **البيان** ملفٌّ ثابت يقرؤه النظام لا Django، والاسم الذي فيه هو ما
      يظهر تحت الأيقونة بعد التثبيت — فلكل لغة ملفٌّ، والقالب يختار.
    * **بطاقة التثبيت** نصُّها في ``pwa-install.js``، وهو ملفٌّ ثابت أيضاً،
      فيُمرَّر إليه مترجَماً عبر ``data-text-*``.
    * **صفحة انقطاع الاتصال** يقدّمها عامل الخدمة من الكاش بلا خادم، فتبدّل
      نصَّها بنفسها اعتماداً على كوكي اللغة.
    """

    def _static(self, relative: str) -> str:
        return (Path(settings.BASE_DIR) / relative).read_text(encoding="utf-8")

    def test_each_language_has_its_own_manifest(self):
        arabic = json.loads(self._static("static/manifest.json"))
        english = json.loads(self._static("static/manifest.en.json"))

        self.assertEqual((arabic["lang"], arabic["dir"]), ("ar", "rtl"))
        self.assertEqual((english["lang"], english["dir"]), ("en", "ltr"))
        self.assertEqual(english["name"], "Tawtheeq")
        # نفس الهوية والنطاق: بيانان للغتين، لا تطبيقان.
        for key in ("id", "start_url", "scope", "display", "theme_color"):
            self.assertEqual(arabic[key], english[key], key)
        self.assertEqual(
            {icon["src"] for icon in arabic["icons"]},
            {icon["src"] for icon in english["icons"]},
        )

    def test_the_service_worker_caches_both_manifests(self):
        """بيانٌ غير مخزَّن يعني تطبيقاً بلا اسمٍ عند أول فتحٍ دون شبكة."""
        worker = self._static("static/sw.js")
        self.assertIn('"/static/manifest.json"', worker)
        self.assertIn('"/static/manifest.en.json"', worker)

    @override_settings(PWA_INSTALL_ENABLED=True)
    def test_the_page_links_the_manifest_of_the_active_language(self):
        self.client.post(
            reverse("set_language"),
            {"language": "en", "next": reverse("reports:landing")},
        )
        html = self.client.get(reverse("reports:landing")).content.decode("utf-8")
        self.assertIn("manifest.en.json", html)

        self.client.post(
            reverse("set_language"),
            {"language": "ar", "next": reverse("reports:landing")},
        )
        html = self.client.get(reverse("reports:landing")).content.decode("utf-8")
        self.assertIn("manifest.json", html)
        self.assertNotIn("manifest.en.json", html)

    def test_the_install_card_carries_its_copy_as_data_attributes(self):
        template = (
            Path(settings.BASE_DIR)
            / "reports/templates/reports/partials/pwa_install.html"
        ).read_text(encoding="utf-8")
        script = self._static("static/js/pwa-install.js")

        for key in ("native", "install-now", "acknowledge", "ios-safari", "generic"):
            with self.subTest(key=key):
                self.assertIn(f'data-text-{key}="{{% translate ', template)
        self.assertIn('getAttribute("data-text-" + key)', script)

    def test_the_offline_page_switches_on_the_language_cookie(self):
        offline = self._static("static/offline.html")

        self.assertIn(settings.LANGUAGE_COOKIE_NAME, offline)
        self.assertIn('data-en="You are offline"', offline)
        self.assertIn('root.dir = "ltr"', offline)


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
