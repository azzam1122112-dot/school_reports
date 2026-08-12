# -*- coding: utf-8 -*-
"""ميزانية سمات ``style`` المضمَّنة — سقفٌ يهبط ولا يرتفع.

**لماذا هذا عددٌ يُقاس لا ذوقٌ يُناقَش.** سمةُ ``style`` واحدة في أي قالب تُلزم
سياسة المحتوى بحمل ``style-src 'unsafe-inline'``. وهذا الإذن يعني أن أي حقن
HTML — عبر حقلٍ لم يُهرَّب، أو مرفقٍ يُعرض، أو رسالة خطأ تُردّد مدخل المستخدم —
يستطيع حقن أنماط: إخفاءُ زرٍّ حقيقي، أو رسمُ نموذج دخولٍ فوق الصفحة، أو تسريبُ
قيم عبر محدّدات السمات.

وسببُ بقاء الإذن مفتوحاً مكتوبٌ صراحةً في ``reports/middleware.py``: «القوالب
تستعمل ``style="..."``». فالعدد أدناه هو المسافة المتبقّية إلى إغلاقه.

**كيف يُستعمل هذا الملف:** كلما نُظِّف قالب، يهبط العدد ويُحدَّث الرقم هنا. ولا
يُرفع الرقم أبداً — ارتفاعُه يعني أن سمةً جديدة أُضيفت، وهي خطوةٌ إلى الوراء في
شيءٍ أمني لا تجميلي. وحين يبلغ صفراً يُحذف ``'unsafe-inline'`` من السياسة
ويُحذف هذا الملف معه.
"""
from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase

INLINE_STYLE = re.compile(r'\sstyle\s*=\s*"([^"]*)"')

# ── السقف الحالي ────────────────────────────────────────────────────────
# قيس في 2026-08-12. البداية كانت 806؛ وأزالت الجولة الأولى 279 سمة
# باستبدال الإعلان المفرد الذي له مقابل صريح في طبقة الأدوات، ثم 32 أخرى
# بنقل أشرطة التقدّم الديناميكية إلى ``data-progress``، ثم 157 بتفكيك
# السمات متعدّدة الإعلانات إلى قوائم أصناف (كلٌّ أو لا شيء لكل سمة)، ثم 305
# برفع الفريد لكل صفحة إلى ``static/css/extracted.css``.
#
# **بلغ العدد أرضيّته: 32.** وكلها خارج حكم سياسة المحتوى:
#   * 31 في قالب بريد HTML — عملاء البريد يتجاهلون ``<link>`` ويجرّدون
#     ``<style>``، فالتضمين هناك شرطُ عملٍ لا دَين.
#   * 1 في قالب PDF يُصيَّر بـWeasyPrint: لا جافاسكربت، ولا يُعرض في متصفّح.
#   * 1 في ``password_reset_email.txt`` وما شابهه.
#
# و``style-src`` أُغلقت فعلاً. فارتفاع هذا العدد لا يعني «دَيناً زاد» بل
# **صفحةً ستُكسر في الإنتاج**: المتصفّح سيرفض السمة الجديدة.
# لا تُضف سمة ``style`` جديدة إلى أي قالب يُعرض في متصفّح.
MAX_INLINE_STYLES = 32

# القوالب التي لم تُنظَّف بعد، مرتّبة بالأثقل. تُحذف أسطرها عند التنظيف.
WORST_OFFENDERS_CEILING = 50


class InlineStyleBudgetTests(SimpleTestCase):
    @property
    def template_root(self) -> Path:
        return Path(settings.BASE_DIR) / "reports" / "templates"

    def _counts(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for path in self.template_root.rglob("*.html"):
            found = INLINE_STYLE.findall(path.read_text(encoding="utf-8"))
            if found:
                counts[str(path.relative_to(self.template_root))] = len(found)
        return counts

    def test_inline_style_count_never_grows(self):
        counts = self._counts()
        total = sum(counts.values())

        self.assertLessEqual(
            total,
            MAX_INLINE_STYLES,
            "أُضيفت سمات style مضمَّنة جديدة. استعمل أصناف "
            "static/css/utilities.css بدلاً منها.",
        )

        if total < MAX_INLINE_STYLES:
            self.fail(
                f"العدد هبط إلى {total} (السقف {MAX_INLINE_STYLES}). "
                f"اخفض MAX_INLINE_STYLES إلى {total} لتثبيت المكسب."
            )

    def test_no_single_template_becomes_a_new_hotspot(self):
        """قالبٌ واحد يتضخّم يُخفي نفسه داخل مجموعٍ لم يتغيّر."""
        offenders = {
            name: count
            for name, count in self._counts().items()
            if count > WORST_OFFENDERS_CEILING
        }
        self.assertEqual(
            offenders,
            {},
            f"قوالب تجاوزت {WORST_OFFENDERS_CEILING} سمة مضمَّنة",
        )

    def test_dynamic_values_use_the_data_attribute_channel(self):
        """القيم الآتية من القاعدة لها مسارها الخاص.

        النسبة المحسوبة لا يمكن أن تكون صنفاً، لكنها لا يجوز أن تبقى في
        ``style`` — فتُكتب في ``data-progress`` ويضبطها الجافاسكربت عبر CSSOM،
        وهو مسارٌ لا يحكمه ``style-src``.
        """
        dynamic = []
        for path in self.template_root.rglob("*.html"):
            for value in INLINE_STYLE.findall(path.read_text(encoding="utf-8")):
                if "{{" in value or "{%" in value:
                    dynamic.append(f"{path.relative_to(self.template_root)}: {value[:60]}")

        # السقف الحالي للقيم الديناميكية المتبقّية.
        self.assertLessEqual(len(dynamic), 35, "\n".join(sorted(dynamic)[:20]))

    def test_every_template_using_utilities_can_actually_load_them(self):
        """صنفٌ بلا ورقته يعني نمطاً مفقوداً — بصمت.

        هذه الحراسة مكتوبة بعد وقوع العطل: جولةُ الاستبدال الآلي وضعت أصنافاً
        في قوالب **مستقلة** لا ترث ``base.html``، فلا تُحمَّل عندها
        ``utilities.css``. والنتيجة صفحةٌ تفقد هوامشها ومحاذاتها بلا أي خطأ.

        وأخطرُ حالة كانت **قالب بريد HTML**: عملاء البريد لا يدعمون أوراق
        الأنماط الخارجية أصلاً، فالأنماط المضمَّنة هناك ليست ديناً بل شرطاً —
        ولذلك يُستثنى مجلد ``emails/`` من التنظيف كلّه.
        """
        uses_utility = re.compile(r'class="[^"]*\bu-[a-z0-9-]+')
        offenders: list[str] = []

        for path in self.template_root.rglob("*.html"):
            rel = str(path.relative_to(self.template_root)).replace("\\", "/")
            if rel.startswith("emails/"):
                continue
            source = path.read_text(encoding="utf-8")
            if not uses_utility.search(source):
                continue
            inherits = "{% extends" in source
            loads_sheet = "css/utilities.css" in source
            is_fragment = "<head" not in source and not inherits
            if inherits or loads_sheet or is_fragment:
                continue
            offenders.append(rel)

        self.assertEqual(
            offenders, [], "قوالب تستعمل أصناف الأدوات دون تحميل utilities.css"
        )

    def test_email_templates_keep_their_inline_styles(self):
        """البريد استثناءٌ دائم لا دَينٌ مؤجَّل.

        عملاء البريد (Outlook خصوصاً) يتجاهلون ``<link>`` وكثيراً ما يجرّدون
        ``<style>``. فالأنماط المضمَّنة هناك هي الطريقة الوحيدة العاملة، ولا
        علاقة لها بسياسة المحتوى لأن الرسالة لا تُعرض في نطاق المنصة.
        """
        email_dir = self.template_root / "emails"
        if not email_dir.exists():
            return
        for path in email_dir.rglob("*.html"):
            with self.subTest(template=path.name):
                self.assertNotIn(
                    'class="u-',
                    path.read_text(encoding="utf-8"),
                    "قالب بريد لا يجوز أن يعتمد على ورقة أنماط خارجية",
                )

    def test_the_utility_layer_exists_and_carries_no_important(self):
        """طبقة الأدوات تعتمد على ترتيب التحميل لا على ``!important``.

        ``!important`` هنا كان سيعيد إنتاج المشكلة نفسها التي جاءت الطبقة
        لحلّها — 519 ``!important`` في المنصة سببُها أن كل طبقة أرادت أن تكسب.
        """
        css = (Path(settings.BASE_DIR) / "static" / "css" / "utilities.css").read_text(
            encoding="utf-8"
        )
        self.assertIn(".u-hidden", css)
        self.assertIn("[data-progress]", css)

        # الفحص على القواعد لا على النثر: التعليق أعلى الملف يشرح لماذا مُنع
        # ``!important`` هنا، فذكرُه فيه ليس استعمالاً له.
        without_comments = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
        self.assertNotIn("!important", without_comments)
