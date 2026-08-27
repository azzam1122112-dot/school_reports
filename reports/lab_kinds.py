# -*- coding: utf-8 -*-
"""The fixed laboratory catalogue, independent from report departments."""
from __future__ import annotations

from django.db import models


class LabKind(models.TextChoices):
    SCIENCE = "science", "مختبر العلوم"
    COMPUTER = "computer", "مختبر الحاسب الآلي"


def infer_lab_kind(*, name: str = "", slug: str = "") -> str:
    """Map a legacy department name/slug to one of the two laboratories."""
    text = f"{name or ''} {slug or ''}".strip().lower()
    if any(token in text for token in ("حاسب", "الحاسب", "computer", "computing", "it-lab")):
        return LabKind.COMPUTER
    if any(token in text for token in ("علوم", "العلوم", "science")):
        return LabKind.SCIENCE
    return ""


LAB_KIND_VALUES = tuple(value for value, _label in LabKind.choices)
