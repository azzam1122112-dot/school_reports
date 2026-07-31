from __future__ import annotations

import json
import re

from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import SubscriptionPlan


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    TRIAL_DAYS=21,
    SITE_URL="https://tawtheeq.example",
)
class LandingPageTests(TestCase):
    def test_landing_explains_the_product_and_has_a_clear_primary_action(self):
        response = self.client.get(reverse("reports:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "إدارة المدرسة")
        self.assertContains(response, "تبدأ من صورة واضحة")
        self.assertContains(response, "ملفات إنجاز المعلمين")
        self.assertContains(response, "التعاميم")
        self.assertContains(response, "الأرشيف")
        self.assertContains(response, "ابدأ تجربة مجانية 21 يوم")
        self.assertContains(response, reverse("reports:register_school"))
        self.assertContains(response, "img/landing/dashboard-system.png")
        self.assertContains(response, "img/landing/tickets-system.png")
        self.assertContains(response, "img/landing/report-system.png")
        self.assertContains(response, "img/landing/archive-system.png")
        self.assertContains(response, "بيانات تجريبية")
        self.assertContains(response, 'id="productLightbox"')
        self.assertContains(response, "img/brand-mark.svg")
        self.assertNotContains(response, "+500K")
        self.assertNotContains(response, "100% رضا")

    def test_landing_has_accessible_navigation_and_single_main_heading(self):
        response = self.client.get(reverse("reports:landing"))
        html = response.content.decode("utf-8")

        self.assertEqual(len(re.findall(r"<h1\b", html, re.IGNORECASE)), 1)
        self.assertIn('href="#mainContent"', html)
        self.assertIn('aria-controls="mobileMenu"', html)
        self.assertIn('aria-expanded="false"', html)
        self.assertIn(
            'id="mobileMenu" role="dialog" aria-modal="true"',
            html,
        )
        self.assertLess(
            html.index("</header>"),
            html.index('id="mobileMenu"'),
            "The fixed mobile menu must stay outside the blurred sticky header.",
        )
        self.assertIn('id="security"', html)
        self.assertIn(f'href="{reverse("reports:terms_conditions")}"', html)
        self.assertIn(f'href="{reverse("reports:refund_policy")}"', html)
        self.assertIn("المنتجات والخدمات والأسعار", html)
        self.assertNotIn("هوية مقدم الخدمة", html)
        self.assertNotIn('href="#business"', html)
        self.assertIn('<span>دخول</span>', html)
        self.assertIn('src="/static/js/landing.js"', html)
        self.assertNotIn("var periodButtons", html)
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_landing_exposes_complete_canonical_and_social_metadata(self):
        response = self.client.get(reverse("reports:landing"))
        html = response.content.decode("utf-8")

        self.assertContains(
            response,
            '<link rel="canonical" href="https://tawtheeq.example/">',
            html=True,
        )
        self.assertIn(
            '<meta name="robots" content="index,follow,max-image-preview:large',
            html,
        )
        self.assertIn('property="og:url" content="https://tawtheeq.example/"', html)
        self.assertIn('name="twitter:card" content="summary_large_image"', html)
        self.assertIn('"@type": "SoftwareApplication"', html)
        self.assertIn('"@type": "WebSite"', html)
        self.assertNotIn("X-Robots-Tag", response.headers)
        schemas = re.findall(
            r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
            html,
            flags=re.DOTALL,
        )
        self.assertEqual(len(schemas), 1)
        self.assertIsInstance(json.loads(schemas[0]), dict)

    @override_settings(CSP_ENABLED=True, CSP_REPORT_ONLY=False)
    def test_landing_embeds_official_sbc_verification_seal(self):
        response = self.client.get(reverse("reports:landing"))
        html = response.content.decode("utf-8")
        seal_origin = "https://eauthenticate.saudibusiness.gov.sa"

        self.assertContains(response, 'class="sbc-verify-seal"')
        self.assertContains(
            response,
            'data-token="SUdjMEt0WXNwNW5IREVVeUNxajRkUT09"',
        )
        self.assertContains(response, 'data-position="bottom-right"')
        self.assertRegex(
            html,
            (
                r'<script\s+nonce="[^"]+"\s+'
                r'src="https://eauthenticate\.saudibusiness\.gov\.sa/'
                r'EAuthSealApi/seal\.js"\s+async\s*></script>'
            ),
        )

        policy = response.headers["Content-Security-Policy"]
        self.assertIn(seal_origin, policy)
        self.assertIn(f"frame-src 'self' {seal_origin}", policy)
        self.assertIn(
            (
                "script-src 'self' "
                f"'nonce-{response.context['CSP_NONCE']}' "
                f"https://cdn.jsdelivr.net {seal_origin}"
            ),
            policy,
        )
        self.assertIn(
            (
                "script-src-elem 'self' "
                f"'nonce-{response.context['CSP_NONCE']}' "
                f"https://cdn.jsdelivr.net {seal_origin}"
            ),
            policy,
        )

    def test_private_pages_send_noindex_header(self):
        response = self.client.get(reverse("reports:login"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers.get("X-Robots-Tag"),
            "noindex, nofollow, noarchive",
        )

    def test_public_content_pages_remain_indexable(self):
        for route_name in (
            "reports:faq",
            "reports:privacy_policy",
            "reports:terms_conditions",
            "reports:refund_policy",
            "reports:service_delivery_policy",
            "reports:complaints_policy",
            "reports:user_guide",
        ):
            response = self.client.get(reverse(route_name))
            self.assertEqual(response.status_code, 200)
            self.assertNotIn("X-Robots-Tag", response.headers)
            self.assertContains(response, "https://tawtheeq.example")

        faq_response = self.client.get(reverse("reports:faq"))
        faq_schema = re.search(
            r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
            faq_response.content.decode("utf-8"),
            flags=re.DOTALL,
        )
        self.assertIsNotNone(faq_schema)
        self.assertEqual(json.loads(faq_schema.group(1))["@type"], "FAQPage")

    def test_active_plans_drive_the_pricing_cards_and_period_switch(self):
        SubscriptionPlan.objects.create(
            name="تجربة المدرسة",
            price=0,
            days_duration=21,
            max_teachers=5,
            description="تشغيل كامل للتجربة\nدعم البدء",
        )
        SubscriptionPlan.objects.create(
            name="مدرسة متوسطة",
            price=650,
            days_duration=180,
            max_teachers=50,
            description="تقارير غير محدودة\nملفات إنجاز",
        )
        SubscriptionPlan.objects.create(
            name="مدرسة متوسطة سنوي",
            price=1250,
            days_duration=365,
            max_teachers=50,
            description="تقارير غير محدودة\nملفات إنجاز",
        )

        response = self.client.get(reverse("reports:landing"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.context["pricing_cards"]), 1)
        self.assertContains(response, 'data-period="6m"')
        self.assertContains(response, 'data-period="1y"')
        self.assertContains(response, "650")
        self.assertContains(response, "1,250")
        self.assertContains(response, "حتى 50 معلماً")
        self.assertContains(response, "تشغيل كامل للتجربة")

    def test_inactive_plans_are_not_advertised(self):
        SubscriptionPlan.objects.create(
            name="باقة قديمة لا تظهر",
            price=999,
            days_duration=365,
            max_teachers=20,
            is_active=False,
        )

        response = self.client.get(reverse("reports:landing"))

        self.assertNotContains(response, "باقة قديمة لا تظهر")
        self.assertEqual(response.context["pricing_cards"], [])
