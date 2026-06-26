# reports/hijri_utils.py
# -*- coding: utf-8 -*-
"""تحويلات التقويم الهجري (أم القرى) — للعرض والإدخال.

كل الدوال آمنة: لا ترفع استثناءات، وتعيد قيمة افتراضية معقولة عند الفشل
(مثل تواريخ خارج المدى المدعوم 1924–2077 ميلادي).
"""
from __future__ import annotations

import datetime as _dt
from typing import Optional

try:
    from hijri_converter import Gregorian, Hijri  # type: ignore
    _HIJRI_OK = True
except Exception:  # pragma: no cover - المكتبة غير مثبّتة محليًا
    _HIJRI_OK = False


_AR_MONTHS = [
    "", "محرّم", "صفر", "ربيع الأول", "ربيع الآخر", "جمادى الأولى", "جمادى الآخرة",
    "رجب", "شعبان", "رمضان", "شوّال", "ذو القعدة", "ذو الحجة",
]
_AR_DAYS = ["الاثنين", "الثلاثاء", "الأربعاء", "الخميس", "الجمعة", "السبت", "الأحد"]


def _as_date(value) -> Optional[_dt.date]:
    if value is None:
        return None
    if isinstance(value, _dt.datetime):
        return value.date()
    if isinstance(value, _dt.date):
        return value
    return None


def to_hijri(value):
    """يحوّل تاريخًا ميلاديًا إلى كائن Hijri، أو None عند التعذّر."""
    d = _as_date(value)
    if d is None or not _HIJRI_OK:
        return None
    try:
        return Gregorian(d.year, d.month, d.day).to_hijri()
    except Exception:
        return None


def hijri_date(value, *, sep: str = "/", fallback: str = "—") -> str:
    """صيغة رقمية: ``1447/11/27``. عند التعذّر يرجع الميلادي ISO أو fallback."""
    h = to_hijri(value)
    if h is None:
        d = _as_date(value)
        return d.isoformat() if d else fallback
    return f"{h.year}{sep}{h.month:02d}{sep}{h.day:02d}"


def hijri_long(value, *, fallback: str = "—") -> str:
    """صيغة طويلة: ``27 ذو القعدة 1447 هـ``."""
    h = to_hijri(value)
    if h is None:
        d = _as_date(value)
        return d.isoformat() if d else fallback
    month = _AR_MONTHS[h.month] if 1 <= h.month <= 12 else str(h.month)
    return f"{h.day} {month} {h.year} هـ"


def hijri_day_name(value, *, fallback: str = "") -> str:
    """اسم اليوم بالعربية اعتمادًا على التاريخ الميلادي."""
    d = _as_date(value)
    if d is None:
        return fallback
    try:
        return _AR_DAYS[d.weekday()]
    except Exception:
        return fallback


def hijri_to_gregorian(year: int, month: int, day: int) -> Optional[_dt.date]:
    """تحويل تاريخ هجري إلى ميلادي (للإدخال). يعيد None عند التعذّر."""
    if not _HIJRI_OK:
        return None
    try:
        g = Hijri(int(year), int(month), int(day)).to_gregorian()
        return _dt.date(g.year, g.month, g.day)
    except Exception:
        return None
