from __future__ import annotations

import json
from pathlib import Path

from django.test import SimpleTestCase


class PlatformBrandingTests(SimpleTestCase):
    project_root = Path(__file__).resolve().parents[2]
    text_extensions = {".html", ".json", ".js", ".md", ".py", ".svg", ".txt"}
    # Only our own sources are checked. Scanning the whole project root also
    # walked build output and local scratch directories (tmp/, staticfiles/,
    # media/, browser profiles), which made the test fail on developer machines
    # and CI for reasons unrelated to branding.
    scanned_roots = ("config", "core", "docs", "maintenance", "reports", "static", "templates")
    excluded_parts = frozenset({
        ".git",
        ".venv",
        ".ruff_cache",
        "__pycache__",
        "migrations",
        "node_modules",
        "tests",
        "tmp",
    })
    forbidden_variants = (
        "منصة توثيقة",
        "منصة تَوثيق",
        "منصة تــوثيق",
        "منصة التقارير المدرسية",
        "منصة التقارير والتذاكر",
        "نظام التقارير المدرسية",
        "نظام توثيق",
    )

    def test_public_brand_name_is_canonical(self):
        failures: list[str] = []

        scanned_files = 0
        for root_name in self.scanned_roots:
            root = self.project_root / root_name
            if not root.is_dir():
                continue

            for path in root.rglob("*"):
                if not path.is_file() or path.suffix.lower() not in self.text_extensions:
                    continue

                relative = path.relative_to(self.project_root)
                if any(part in self.excluded_parts for part in relative.parts):
                    continue

                try:
                    content = path.read_text(encoding="utf-8")
                except (UnicodeDecodeError, OSError):
                    # A stray binary file with a text extension must not fail
                    # the branding check.
                    continue

                scanned_files += 1
                for variant in self.forbidden_variants:
                    if variant in content:
                        failures.append(f"{relative}: {variant}")

        self.assertGreater(scanned_files, 0, "لم يتم فحص أي ملف — تحقق من قائمة المجلدات.")

        self.assertEqual(failures, [], "وجدت أسماء قديمة للمنصة:\n" + "\n".join(failures))

    def test_pwa_uses_full_platform_name(self):
        manifest = json.loads((self.project_root / "static" / "manifest.json").read_text(encoding="utf-8"))

        self.assertEqual(manifest["name"], "منصة توثيق")
        self.assertEqual(manifest["short_name"], "منصة توثيق")
