# reports/models.py
# -*- coding: utf-8 -*-
"""Compatibility facade for the reports app models.

The model definitions are split by domain under ``reports.model_parts``.
Keep importing from ``reports.models`` throughout the project so existing views,
admin classes, tests, and historical migrations continue to work.
"""
from __future__ import annotations

from .model_parts import *  # noqa: F401,F403


def _pin_migration_callable_paths() -> None:
    """Keep historical migration callable paths stable after the split."""
    names = (
        "_achievement_pdf_upload_to",
        "_achievement_evidence_upload_to",
        "_achievement_report_evidence_upload_to",
        "_leadership_evidence_upload_to",
        "_payment_receipt_upload_to",
        "_report_image_upload_to",
        "_report_evidence_upload_to",
        "_ticket_attachment_upload_to",
        "_notification_attachment_upload_to",
        "_school_logo_upload_to",
        "_ticket_image_upload_to",
        "validate_attachment_size",
    )
    for name in names:
        obj = globals().get(name)
        if obj is not None:
            try:
                obj.__module__ = __name__
            except Exception:
                pass


_pin_migration_callable_paths()
