# -*- coding: utf-8 -*-
"""مصدر واحد لقائمة السنوات الدراسية الهجرية المعروضة للاختيار.

**لماذا مركزيّاً؟** لأن القائمة كانت تُبنى في موضعين بمنطقين مختلفين: شاشة
«بيانات المدرسة» تقرأ السنوات المركزية التي يديرها مالك المنصة، ونموذج رفع
الوثائق يقرأ حقلَي المدرسة وحدهما. والمدرسة الجديدة تُنشأ وحقلاها فارغان، فكان
حقلُ السنة في نموذج الوثائق **إلزامياً وبلا خيار واحد**: كل رفع يفشل برسالة
«السنة الدراسية أول ما يُقلَّب به الأرشيف» التي تصف الحقل ولا تدلّ على السبب،
وأرشيف الوثائق معطّل من أول يوم في كل مدرسة لم يمرّ مديرها على الإعدادات.

الترتيب هنا: ما تحفظه المدرسة، ثم ما يديره مالك المنصة، ثم نطاقٌ محسوب لا
يترك القائمة فارغة أبداً.
"""
from __future__ import annotations

import datetime
import re

__all__ = [
    "ACADEMIC_YEAR_RE",
    "approx_current_hijri_year",
    "hijri_academic_year_options",
]

ACADEMIC_YEAR_RE = re.compile(r"^\d{4}-\d{4}$")


def approx_current_hijri_year() -> int:
    """تقدير السنة الهجرية الحالية (يكفي لتوليد نطاق اختيار واسع)."""
    today = datetime.date.today()
    g = today.year + (today.month - 1) / 12.0
    try:
        return int(round((g - 622) * 33.0 / 32.0))
    except (TypeError, ValueError, OverflowError):
        return 1447


def _saved_years(instance) -> set[str]:
    """ما تحفظه المدرسة فعلاً — يُضمّ دائماً كي لا تختفي قيمة مستعملة."""
    saved: set[str] = set()
    current = (getattr(instance, "current_academic_year", "") or "").strip()
    if ACADEMIC_YEAR_RE.match(current):
        saved.add(current)
    for year in getattr(instance, "allowed_academic_years", None) or []:
        value = str(year).strip()
        if ACADEMIC_YEAR_RE.match(value):
            saved.add(value)
    return saved


def _central_years() -> list[str]:
    """السنوات النشطة التي يديرها مالك المنصة."""
    try:
        from .models import AcademicYear

        return [
            str(value)
            for value in AcademicYear.objects.filter(is_active=True).values_list("value", flat=True)
        ]
    except Exception:
        # القائمة المركزية وسيلةُ راحة لا شرطُ عمل: تعذُّر قراءتها يسقطنا إلى
        # النطاق المحسوب أدناه، ولا يُعطّل الشاشة التي تطلبها.
        return []


def hijri_academic_year_options(instance=None) -> list[str]:
    """قائمة سنوات دراسية هجرية صالحة للعرض، مرتَّبة ولا تكون فارغة.

    ``instance`` مدرسةٌ اختيارية: قيمها المحفوظة تُضمّ إلى القائمة حتى لو
    خرجت من القائمة المركزية.
    """
    saved = _saved_years(instance) if instance is not None else set()

    central = [value for value in _central_years() if ACADEMIC_YEAR_RE.match(value)]
    if central:
        return sorted(set(central) | saved)

    # ── تراجع: نطاق محسوب حول أحدث سنة معروفة ──
    starts: set[int] = set()
    candidates: list[int] = [approx_current_hijri_year()]
    for value in saved:
        start = int(value.split("-")[0])
        starts.add(start)
        candidates.append(start)

    # المرتكز أحدثُ قيمة: يضمن ظهور السنوات القادمة ولو كانت المحفوظة قديمة.
    anchor = max(candidates)
    for start in range(anchor - 3, anchor + 6):
        starts.add(start)

    return [f"{start}-{start + 1}" for start in sorted(starts)]
