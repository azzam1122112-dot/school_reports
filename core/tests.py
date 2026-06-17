from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase, override_settings

from core.views import healthz


class HealthzTests(TestCase):
    @override_settings(
        CACHES={"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"}},
    )
    def test_healthz_skips_channel_probe_by_default(self):
        with patch("core.views.os.getenv", return_value=""):
            with patch("channels.layers.get_channel_layer") as get_layer:
                response = healthz(object())

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'"channels": "skipped"', response.content)
        get_layer.assert_not_called()
