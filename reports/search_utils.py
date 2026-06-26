# reports/search_utils.py
# -*- coding: utf-8 -*-
"""بحث ذكي عربي اللغة عبر عدة حقول.

المزايا:
- تطبيع عربي: توحيد الألف (أ/إ/آ/ٱ→ا)، التاء المربوطة (ة→ه)، الألف المقصورة (ى→ي)،
  والهمزات (ؤ→و، ئ→ي)، مع إزالة التشكيل والتطويل (ـ).
- مطابقة غير حساسة لتلك الفروق عبر تعبير نمطي (iregex) يقبل كل صيغ الحرف.
- دعم عدة كلمات: كل كلمة يجب أن تظهر (AND) في أيٍّ من الحقول (OR).
"""
from __future__ import annotations

import re
from typing import Iterable

from django.db.models import Q

# تشكيل + ألف خنجرية + تطويل
_DIACRITICS_RE = re.compile(r"[ً-ْٰـ]")

# توحيد الأحرف عند التطبيع
_NORMALIZE_MAP = {
    "أ": "ا", "إ": "ا", "آ": "ا", "ٱ": "ا",
    "ة": "ه",
    "ى": "ي", "ئ": "ي",
    "ؤ": "و",
}
_NORMALIZE_TABLE = str.maketrans(_NORMALIZE_MAP)

# عند بناء النمط: كل حرف مُطبّع يُقابَل بكل صيغه الممكنة
_VARIANT_CLASSES = {
    "ا": "اأإآٱ",
    "ه": "هة",
    "ي": "يىئ",
    "و": "وؤ",
}

# يُسمح بوجود تشكيل/تطويل اختياري بين الأحرف في القيمة المخزّنة
_OPTIONAL_MARKS = r"[ً-ْٰـ]*"

# حد أقصى لطول كل كلمة بحث (حماية من أنماط ثقيلة)
_MAX_TERM_LEN = 64
# حد أقصى لعدد الكلمات
_MAX_TERMS = 8


def normalize_arabic(text: str) -> str:
    """يُعيد نصًّا مُطبّعًا (يصلح للمقارنة أو العرض)."""
    if not text:
        return ""
    text = _DIACRITICS_RE.sub("", str(text))
    text = text.translate(_NORMALIZE_TABLE)
    return text.strip().lower()


def _char_pattern(ch: str) -> str:
    if ch in _VARIANT_CLASSES:
        return "[" + _VARIANT_CLASSES[ch] + "]"
    return re.escape(ch)


def _term_iregex(term: str) -> str:
    """يبني تعبيرًا نمطيًا يطابق الكلمة بغضّ النظر عن صيغ الحروف والتشكيل."""
    term = normalize_arabic(term)[:_MAX_TERM_LEN]
    return _OPTIONAL_MARKS.join(_char_pattern(c) for c in term if not c.isspace())


def smart_search_q(query: str, fields: Iterable[str]) -> Q:
    """يبني Q للبحث الذكي عبر الحقول المعطاة.

    كل كلمة في الاستعلام يجب أن تظهر (AND)، وداخل كل كلمة نطابق أيًّا من الحقول (OR).
    يُعيد Q فارغًا (محايدًا) إن كان الاستعلام فارغًا.
    """
    fields = [f for f in fields if f]
    if not query or not fields:
        return Q()

    terms = [t for t in str(query).split() if t][:_MAX_TERMS]
    combined = Q()
    for term in terms:
        pattern = _term_iregex(term)
        if not pattern:
            continue
        term_q = Q()
        for field in fields:
            term_q |= Q(**{f"{field}__iregex": pattern})
        combined &= term_q
    return combined


# الحقول الافتراضية لكل نطاق
REPORT_SEARCH_FIELDS = (
    "title",
    "idea",
    "teacher_name",
    "teacher__name",
    "category__name",
    "day_name",
    "academic_year",
)

ACHIEVEMENT_TEACHER_SEARCH_FIELDS = (
    "name",
    "phone",
    "national_id",
)
