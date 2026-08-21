"""Canonical object-key layout for tenant files.

R2 is a flat object store.  The ``/`` characters in an object key are prefixes
that the dashboard and S3-compatible clients present as folders.  Every
school-owned file therefore starts with ``schools/<immutable-storage-key>/``.
"""
from __future__ import annotations

import os
import secrets
from typing import Iterable

from django.utils.text import get_valid_filename, slugify


SCHOOLS_ROOT = "schools"
PLATFORM_ROOT = "platform"


def safe_segment(value, *, fallback: str) -> str:
    """Return one safe, slash-free object-key segment."""
    raw = str(value or "").strip().replace("\\", "-").replace("/", "-")
    cleaned = slugify(raw, allow_unicode=False) or get_valid_filename(raw)
    cleaned = (cleaned or fallback).strip("._-") or fallback
    return cleaned[:96]


def safe_unique_filename(filename: str, *, fallback: str = "file") -> str:
    """Keep a readable basename while preventing collisions and traversal."""
    base = os.path.basename((filename or fallback).replace("\\", "/"))
    base = get_valid_filename(base) or fallback
    stem, extension = os.path.splitext(base)
    stem = (stem or fallback)[:80].strip("._-") or fallback
    extension = (extension or "").lower()[:16]
    return f"{stem}_{secrets.token_hex(8)}{extension}"


def school_storage_key(school) -> str:
    """Resolve the immutable tenant key, with compatibility for old instances."""
    if school is None:
        raise ValueError("A school is required for a school-owned file.")
    value = (
        getattr(school, "storage_key", "")
        or getattr(school, "code", "")
        or (f"school-{getattr(school, 'pk', '')}" if getattr(school, "pk", None) else "")
    )
    if not value:
        raise ValueError("The school must be saved before uploading its files.")
    return safe_segment(value, fallback="school")


def _clean_parts(parts: Iterable[object]) -> list[str]:
    return [safe_segment(part, fallback="item") for part in parts if part not in (None, "")]


def school_file_path(
    school,
    category: str,
    filename: str,
    *,
    parts: Iterable[object] = (),
    fallback: str = "file",
) -> str:
    """Build a collision-resistant key beneath the school's fixed prefix."""
    segments = [
        SCHOOLS_ROOT,
        school_storage_key(school),
        *_clean_parts(str(category).split("/")),
        *_clean_parts(parts),
        safe_unique_filename(filename, fallback=fallback),
    ]
    return "/".join(segments)


def school_or_platform_file_path(
    school,
    category: str,
    filename: str,
    *,
    parts: Iterable[object] = (),
    fallback: str = "file",
) -> str:
    """Use the school prefix when owned by one school, otherwise platform/."""
    if school is not None:
        return school_file_path(
            school,
            category,
            filename,
            parts=parts,
            fallback=fallback,
        )
    segments = [
        PLATFORM_ROOT,
        *_clean_parts(str(category).split("/")),
        *_clean_parts(parts),
        safe_unique_filename(filename, fallback=fallback),
    ]
    return "/".join(segments)


def is_in_school_prefix(name: str, school) -> bool:
    prefix = f"{SCHOOLS_ROOT}/{school_storage_key(school)}/"
    return str(name or "").lstrip("/").startswith(prefix)
