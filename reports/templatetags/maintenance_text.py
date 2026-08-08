from __future__ import annotations

import re

from django import template
from django.utils.html import escape
from django.utils.safestring import mark_safe

register = template.Library()


def _inline_markdown(value: str) -> str:
    text = escape(value)
    return re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", text)


@register.filter(name="maintenance_markdown")
def maintenance_markdown(value: str) -> str:
    """Render a safe, small Markdown subset for the maintenance message.

    Supports headings, bold text, simple bullets, and paragraphs. Raw HTML stays
    escaped, which keeps the admin-editable message safe to display.
    """

    raw = (value or "").replace("\r\n", "\n").replace("\r", "\n").strip()
    if not raw:
        return ""

    parts: list[str] = []
    paragraph: list[str] = []
    bullets: list[str] = []

    def flush_paragraph() -> None:
        if paragraph:
            parts.append(f"<p>{_inline_markdown(' '.join(paragraph))}</p>")
            paragraph.clear()

    def flush_bullets() -> None:
        if bullets:
            items = "".join(f"<li>{_inline_markdown(item)}</li>" for item in bullets)
            parts.append(f"<ul>{items}</ul>")
            bullets.clear()

    for line in raw.split("\n"):
        stripped = line.strip()
        if not stripped:
            flush_paragraph()
            flush_bullets()
            continue

        heading = re.match(r"^(#{1,3})\s+(.+)$", stripped)
        if heading:
            flush_paragraph()
            flush_bullets()
            level = min(3, len(heading.group(1)))
            parts.append(f"<h{level}>{_inline_markdown(heading.group(2).strip())}</h{level}>")
            continue

        bullet = re.match(r"^[-*]\s+(.+)$", stripped)
        if bullet:
            flush_paragraph()
            bullets.append(bullet.group(1).strip())
            continue

        flush_bullets()
        paragraph.append(stripped)

    flush_paragraph()
    flush_bullets()
    # آمنٌ لأن كل قيمة قادمة من المستخدم تمرّ بـ ``escape`` داخل
    # ``_inline_markdown`` قبل أن تُركَّب، والوسوم المُدرَجة هنا حرفية لا مبنية
    # من المدخل. لو نُقل الهروب إلى ما بعد التركيب لصار هذا السطر ثغرة XSS.
    return mark_safe("".join(parts))  # noqa: S308
