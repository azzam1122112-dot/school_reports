# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Iterable, Optional

from .models import Department, Role

LEGACY_TEACHER_ROLE_SLUG = "teacher"
LEGACY_MANAGER_ROLE_SLUG = "manager"

# Inventory of UI surfaces that still write to Teacher.role for compatibility only.
# Once legacy Role-based screens are fully removed, this module should become the
# single deletion point for that compatibility layer.
LEGACY_ROLE_WRITE_SURFACES = (
    "forms.TeacherForm",
    "forms.TeacherCreateForm",
    "forms.TeacherEditForm",
    "forms.PlatformAdminCreateForm",
    "views.achievements.report_viewer_create",
    "views.achievements.report_viewer_update",
)


def current_legacy_role_slug(user) -> Optional[str]:
    try:
        return (getattr(getattr(user, "role", None), "slug", None) or "").strip().lower() or None
    except Exception:
        return None


def _legacy_role_defaults(slug: str) -> dict | None:
    normalized = (slug or "").strip().lower()
    if normalized == LEGACY_TEACHER_ROLE_SLUG:
        return {
            "name": "المعلم",
            "is_staff_by_default": False,
            "can_view_all_reports": False,
            "is_active": True,
        }
    return None


def get_legacy_role(slug: str | None, *, create_missing: bool = False) -> Optional[Role]:
    normalized = (slug or "").strip().lower()
    if not normalized:
        return None

    try:
        if create_missing:
            defaults = _legacy_role_defaults(normalized)
            if defaults is not None:
                role_obj, _ = Role.objects.get_or_create(slug=normalized, defaults=defaults)
                return role_obj
        return Role.objects.filter(slug=normalized).first()
    except Exception:
        return None


def assign_legacy_role(user, *, slug: str | None, create_missing: bool = False) -> Optional[Role]:
    role_obj = get_legacy_role(slug, create_missing=create_missing)
    try:
        user.role = role_obj
    except Exception:
        pass
    return role_obj


def sync_legacy_teacher_role(user, *, create_missing: bool = False) -> Optional[Role]:
    return assign_legacy_role(
        user,
        slug=LEGACY_TEACHER_ROLE_SLUG,
        create_missing=create_missing,
    )


def legacy_role_slug_for_department(
    department: Optional[Department],
    *,
    teacher_department_slugs: Iterable[str],
) -> Optional[str]:
    if department is None:
        return None

    try:
        dept_slug = (getattr(department, "slug", None) or "").strip().lower()
    except Exception:
        dept_slug = ""
    if not dept_slug:
        return None

    normalized_teacher_slugs = {str(v).strip().lower() for v in teacher_department_slugs if str(v).strip()}
    return LEGACY_TEACHER_ROLE_SLUG if dept_slug in normalized_teacher_slugs else dept_slug


def sync_legacy_role_for_department(
    user,
    department: Optional[Department],
    *,
    teacher_department_slugs: Iterable[str],
    create_missing: bool = False,
) -> Optional[Role]:
    return assign_legacy_role(
        user,
        slug=legacy_role_slug_for_department(
            department,
            teacher_department_slugs=teacher_department_slugs,
        ),
        create_missing=create_missing,
    )
