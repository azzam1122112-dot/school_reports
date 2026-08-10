from __future__ import annotations

import json
import re

from django.test import TestCase, override_settings
from django.urls import reverse


@override_settings(
    ALLOWED_HOSTS=["testserver"],
    SITE_URL="https://canonical.example.test",
)
class PublicSeoTests(TestCase):
    PUBLIC_ROUTES = (
        "reports:landing",
        "reports:faq",
        "reports:user_guide",
        "reports:privacy_policy",
        "reports:terms_conditions",
        "reports:refund_policy",
        "reports:service_delivery_policy",
        "reports:complaints_policy",
    )

    def test_every_public_page_has_complete_share_and_canonical_metadata(self):
        for route_name in self.PUBLIC_ROUTES:
            with self.subTest(route=route_name):
                path = reverse(route_name)
                response = self.client.get(path)
                html = response.content.decode("utf-8")

                self.assertEqual(response.status_code, 200)
                self.assertNotIn("X-Robots-Tag", response.headers)
                self.assertEqual(html.count('<meta name="description"'), 1)
                self.assertIn(
                    f'<link rel="canonical" href="https://canonical.example.test{path}">',
                    html,
                )
                for marker in (
                    'property="og:title"',
                    'property="og:description"',
                    'property="og:url"',
                    'property="og:image"',
                    'property="og:image:alt"',
                    'name="twitter:card" content="summary_large_image"',
                    'name="twitter:title"',
                    'name="twitter:description"',
                    'name="twitter:image"',
                    'name="twitter:image:alt"',
                ):
                    self.assertIn(marker, html)

    def test_structured_data_on_public_pages_is_valid_json(self):
        for route_name in self.PUBLIC_ROUTES:
            with self.subTest(route=route_name):
                response = self.client.get(reverse(route_name))
                schemas = re.findall(
                    r'<script type="application/ld\+json"[^>]*>(.*?)</script>',
                    response.content.decode("utf-8"),
                    flags=re.DOTALL,
                )
                self.assertTrue(schemas)
                for schema in schemas:
                    self.assertIsInstance(json.loads(schema), dict)

    def test_private_account_page_remains_explicitly_non_indexable(self):
        response = self.client.get(reverse("reports:login"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.headers["X-Robots-Tag"],
            "noindex, nofollow, noarchive",
        )
        self.assertContains(
            response,
            '<meta name="robots" content="noindex,nofollow,noarchive">',
            html=True,
        )

