# -*- coding: utf-8 -*-
"""دليل الاستخدام: أن يبقى مواكباً للمنصة، لا أن يبقى موجوداً.

الدليل تقادم مرة: بقي يشرح دورين — المعلّم ومدير المدرسة — بينما صارت في
المنصة خمسة أدوار، وشُحنت ميزات كاملة (التكليفات، الاجتماعات، الخطط،
الاعتماد، الأرشيف، الإملاء الصوتي) بلا سطر واحد عنها.

ولذلك لا تكتفي هذه الاختبارات بالتحقق من أن الصفحة تُفتح. هي تربط الدليل
بمصدر الحقيقة في الشيفرة: كل دور في ``SchoolMembership.RoleType`` يلزمه تبويب،
وكل تبويب يلزمه محتوى، وكل رابط في الفهرس يلزمه هدف. فإن أُضيف دور جديد ولم
يُشرح، سقط الاختبار قبل أن يكتشفه مستخدم.
"""
from __future__ import annotations

import re
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from reports.models import SchoolMembership

# ترجمة دور الشيفرة إلى قيمة التصفية في الدليل.
ROLE_TO_GUIDE_FILTER = {
    SchoolMembership.RoleType.TEACHER: "teacher",
    SchoolMembership.RoleType.MANAGER: "manager",
    SchoolMembership.RoleType.DEPUTY: "deputy",
    SchoolMembership.RoleType.ADMIN_STAFF: "admin-staff",
}


def _guide_source() -> str:
    root = Path(settings.BASE_DIR)
    return (
        (root / "reports/templates/reports/user_guide.html").read_text(encoding="utf-8")
        + (root / "reports/templates/reports/partials/user_guide_content.html").read_text(encoding="utf-8")
    )


class UserGuideStructureTests(SimpleTestCase):
    def setUp(self):
        self.source = _guide_source()
        self.filters = set(re.findall(r'data-guide-filter="([a-z-]+)"', self.source))
        self.topic_roles = re.findall(r'data-guide-role="([a-z-]+)"', self.source)

    def test_every_role_in_the_code_has_a_tab_in_the_guide(self):
        """دورٌ يعمل في المنصة ولا يجد نفسه في الدليل هو الشكوى التي بدأت هذا العمل."""
        for role, guide_filter in ROLE_TO_GUIDE_FILTER.items():
            with self.subTest(role=role.label):
                self.assertIn(
                    guide_filter,
                    self.filters,
                    f"الدور «{role.label}» موجود في الشيفرة وليس له تبويب في الدليل",
                )

    def test_the_group_executive_role_is_covered_too(self):
        self.assertIn("executive", self.filters)

    def test_every_tab_actually_has_topics(self):
        """تبويبٌ يفتح على فراغ أسوأ من غيابه."""
        for guide_filter in self.filters:
            if guide_filter == "all":
                continue
            with self.subTest(tab=guide_filter):
                self.assertGreaterEqual(
                    self.topic_roles.count(guide_filter),
                    1,
                    f"التبويب «{guide_filter}» بلا موضوعات",
                )

    def test_every_topic_role_has_a_tab_that_reaches_it(self):
        """موضوعٌ بدورٍ بلا تبويب لا يظهر إلا في العرض الكامل."""
        for role in set(self.topic_roles):
            if role == "all":
                continue
            with self.subTest(role=role):
                self.assertIn(role, self.filters)

    def test_no_index_link_points_at_a_missing_topic(self):
        ids = set(re.findall(r'<section class="guide-topic[^"]*" id="([a-z-]+)"', self.source))
        index_links = set(re.findall(r'<a href="#([a-z-]+)"', self.source))
        # روابط خارج الفهرس (مثل العودة لأعلى) لا تشير إلى موضوعات.
        dangling = {link for link in index_links if link not in ids} - {"start", "top", "pageTop"}

        self.assertEqual(dangling, set(), f"روابط فهرس بلا هدف: {sorted(dangling)}")

    def test_shipped_features_are_documented(self):
        """كل ميزة هنا مشحونة فعلاً؛ غيابها عن الدليل يعني مستخدماً لا يعرف أنها موجودة."""
        required = {
            "دورة الاعتماد": "approval-cycle",
            "الإملاء الصوتي": "teacher-voice",
            "التكليفات": "teacher-assignments",
            "روابط المشاركة وسلة المحذوفات": "teacher-sharing",
            "الأدوار والنطاقات": "manager-roles",
            "أنواع التقارير ومسارها": "manager-report-types",
            "صندوق الاعتماد": "manager-approvals",
            "اعتماد ملفات الإنجاز": "manager-achievement",
            "الاجتماعات والمجالس": "manager-meetings",
            "الخطط والمبادرات": "manager-plans",
            "الأرشيف والتخزين": "manager-archive",
            "سجل العمليات": "manager-audit",
            "المختبر": "staff-lab",
            "لوحة المجموعة": "executive-overview",
            "تثبيت التطبيق وإشعارات الجهاز": "app-install",
            "المساعد الذكي": "assistant",
            "بيانات المستخدم": "my-data",
        }
        for label, topic_id in required.items():
            with self.subTest(feature=label):
                self.assertIn(f'id="{topic_id}"', self.source, f"«{label}» غير موثقة في الدليل")

    def test_every_topic_is_searchable(self):
        """البحث في الدليل يقرأ ``data-guide-title``؛ موضوع بلا كلمات مفتاحية لا يُعثر عليه."""
        topics = re.findall(r'<section class="guide-topic[^"]*"[^>]*>', self.source)
        without_keywords = [t for t in topics if "data-guide-title=" not in t]

        self.assertEqual(without_keywords, [], "موضوعات بلا كلمات بحث")


class UserGuidePageTests(TestCase):
    def test_the_guide_renders_with_every_role_tab(self):
        response = self.client.get(reverse("reports:user_guide"))

        self.assertEqual(response.status_code, 200)
        for guide_filter in ("teacher", "deputy", "admin-staff", "manager", "executive"):
            with self.subTest(tab=guide_filter):
                self.assertContains(response, f'data-guide-filter="{guide_filter}"')

    def test_the_guide_explains_the_approval_rules_it_enforces(self):
        """القاعدتان مفروضتان في ``services_approval``؛ المستخدم يستحق معرفتهما."""
        response = self.client.get(reverse("reports:user_guide"))

        self.assertContains(response, "لا أحد يعتمد عمله بنفسه")
        self.assertContains(response, "لا يُعدَّل ولا يُحذف")
