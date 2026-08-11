#!/usr/bin/env python3
"""يتحقق أن ``requirements.lock.txt`` يغطي كل تثبيت مباشر في ``requirements.txt``.

**لماذا ليس ``pip-compile`` ثم ``diff``؟** لأن ``pip-compile`` يحلّ إلى أحدث ما
هو منشور، فأي إصدار جديد لأي اعتمادية غير مباشرة يجعل الفرق غير صفري. وفحصٌ
يحمرّ لأسباب لا علاقة لها بالتعديل المقترح يُدرَّب الفريق على تجاهله — فيصير
وجوده أسوأ من غيابه.

فالمُتحقَّق منه هنا هو الانحراف الحقيقي وحده: أن يُعدَّل ``requirements.txt``
ولا يُعاد توليد القفل. عندها يكون ما يُفحَص غير ما يُبنى، وهي الحالة التي أدخلت
إصدارات ذات ثغرات معروفة إلى الإنتاج دون أن تظهر في أي فحص.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIRECT = ROOT / "requirements.txt"
LOCK = ROOT / "requirements.lock.txt"

_PIN = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._-]*)\s*(\[[^\]]*\])?\s*==\s*([^\s;#\\]+)")


def _normalise(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _read_pins(path: Path) -> dict[str, str]:
    pins: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith(("#", "-")):
            continue
        match = _PIN.match(stripped)
        if match:
            pins[_normalise(match.group(1))] = match.group(3)
    return pins


def main() -> int:
    if not LOCK.exists():
        print("requirements.lock.txt is missing. Generate it with:", file=sys.stderr)
        print(
            "  pip-compile --generate-hashes --strip-extras "
            "--output-file=requirements.lock.txt requirements.txt",
            file=sys.stderr,
        )
        return 1

    direct = _read_pins(DIRECT)
    locked = _read_pins(LOCK)

    problems: list[str] = []
    for name, version in sorted(direct.items()):
        if name not in locked:
            problems.append(f"  {name}=={version} is pinned directly but absent from the lock")
        elif locked[name] != version:
            problems.append(
                f"  {name}: requirements.txt pins {version}, lock has {locked[name]}"
            )

    # القفل بلا تجزئات ليس قفلاً: التثبيت بـ ``--require-hashes`` سيفشل، وفحص
    # الثغرات سيقرأ ملفاً لا يصف ما يُبنى.
    if "--hash=" not in LOCK.read_text(encoding="utf-8"):
        problems.append("  the lock file carries no --hash entries")

    if problems:
        print("requirements.lock.txt is out of sync with requirements.txt:", file=sys.stderr)
        print("\n".join(problems), file=sys.stderr)
        print(
            "\nRegenerate it:\n"
            "  pip-compile --generate-hashes --strip-extras "
            "--output-file=requirements.lock.txt requirements.txt",
            file=sys.stderr,
        )
        return 1

    print(f"Lock covers all {len(direct)} direct pins ({len(locked)} packages total).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
