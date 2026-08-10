from __future__ import annotations

import base64
import io
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from deploy.hetzner.apply_runtime_config import (
    _assert_web_push_can_boot,
    _collect,
    _rewrite,
)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


class WebPushRuntimeConfigTests(SimpleTestCase):
    def _args(self, **overrides):
        values = {
            "tamara_enabled": None,
            "moyasar_enabled": None,
            "moyasar_environment": None,
            "pdf_offload_enabled": None,
            "celery_media_concurrency": None,
            "web_concurrency": None,
            "moyasar_key_from_stdin": False,
            "web_push_enabled": "True",
            "web_push_config_from_stdin": True,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_valid_vapid_pair_is_collected_without_printing_or_reformatting(self):
        private_key = _b64(b"p" * 32)
        public_key = _b64(b"\x04" + b"q" * 64)
        stdin = io.StringIO(f"{private_key}\n{public_key}\nmailto:test@example.com\n")
        with patch("sys.stdin", stdin):
            values = _collect(self._args())
        self.assertEqual(values["WEB_PUSH_ENABLED"], "True")
        self.assertEqual(values["WEB_PUSH_VAPID_PRIVATE_KEY"], private_key)
        self.assertEqual(values["WEB_PUSH_VAPID_PUBLIC_KEY"], public_key)

    def test_enable_is_rejected_when_server_has_no_keys(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            path = Path(directory) / "env.production"
            path.write_text("WEB_PUSH_ENABLED=False\n", encoding="utf-8")
            with self.assertRaisesMessage(SystemExit, "both stable VAPID keys"):
                _assert_web_push_can_boot(
                    path,
                    {"WEB_PUSH_ENABLED": "True"},
                )

    def test_rewrite_preserves_unrelated_production_values(self):
        from tempfile import TemporaryDirectory

        with TemporaryDirectory() as directory:
            path = Path(directory) / "env.production"
            path.write_text("SECRET_KEY=untouched\nWEB_PUSH_ENABLED=False\n", encoding="utf-8")
            changed = _rewrite(
                path,
                {"WEB_PUSH_ENABLED": "True", "WEB_PUSH_SUBJECT": "mailto:test@example.com"},
            )
            content = path.read_text(encoding="utf-8")
        self.assertIn("SECRET_KEY=untouched", content)
        self.assertIn("WEB_PUSH_ENABLED=True", content)
        self.assertEqual(set(changed), {"WEB_PUSH_ENABLED", "WEB_PUSH_SUBJECT"})
