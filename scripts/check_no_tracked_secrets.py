#!/usr/bin/env python3
"""يمنع عودة ملفات الأسرار إلى تتبع Git.

**لماذا لا يكفي ``.gitignore``؟** لأنه يحكم ما *يُضاف*، لا ما هو مُضاف. ملفٌ
دخل التتبع مرة يبقى متتبَّعاً وإن أُدرج في ``.gitignore`` بعدها — وهذا بالضبط
ما جرى لـ``.env`` هنا: ظلّ متتبَّعاً عبر 19 التزاماً، ثم أُزيل، وبقيت نسخه في
التاريخ بما فيها مفتاح توقيع إنتاج وسلسلة اتصال بقاعدة البيانات.

وإزالة سرٍّ من التاريخ تعني إعادة كتابة كل المعرِّفات وتنسيقاً مع كل من يملك
نسخة، أو تدوير السرّ. وكلاهما أغلى بكثير من هذا الفحص.
"""
from __future__ import annotations

import subprocess
import sys

# أنماط ``git ls-files`` — ما لا ينبغي أن يكون متتبَّعاً أبداً.
SECRET_PATTERNS = (
    ".env",
    ".env.*",
    "deploy/hetzner/env.production",
    "deploy/hetzner/env.redis",
    "deploy/hetzner/env.postgres",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa",
    "id_ed25519",
    "*.sqlite3",
    "*.sqlite3.*",
)

# القوالب مقصودة: هي التوثيق الذي يمنع الحاجة إلى الملف الحقيقي في المستودع.
ALLOWED_SUFFIXES = (".example", ".sample", ".template", ".dist")


def main() -> int:
    try:
        # قائمة وسائط ثابتة بلا صدفة ولا مدخلات خارجية: ``SECRET_PATTERNS``
        # ثابت في هذا الملف، و``--`` يمنع تأويل أي نمط كخيار. و``git`` يُحلّ من
        # المسار عمداً — تثبيت مسار مطلق يكسر السكربت على ويندوز وmacOS ولينكس
        # معاً، وهي البيئات الثلاث التي يعمل فيها هذا الخطّاف.
        result = subprocess.run(  # noqa: S603
            ["git", "ls-files", "--", *SECRET_PATTERNS],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        print(f"تعذّر استجواب Git: {exc}", file=sys.stderr)
        return 1

    offenders = [
        stripped
        for line in result.stdout.split("\n")
        if (stripped := line.strip()) and not stripped.endswith(ALLOWED_SUFFIXES)
    ]

    if offenders:
        print("ملفات أسرار داخل تتبع Git:", file=sys.stderr)
        for path in offenders:
            print(f"  {path}", file=sys.stderr)
        print(
            "\nأخرجها من التتبع مع إبقائها على القرص:\n"
            "  git rm --cached <path>\n"
            "وإن كانت قد وصلت مستودعاً بعيداً فدوّر ما فيها — الحذف لا يمحو التاريخ.",
            file=sys.stderr,
        )
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
