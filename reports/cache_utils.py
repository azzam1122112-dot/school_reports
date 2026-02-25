# reports/cache_utils.py
# -*- coding: utf-8 -*-
"""
Centralized cache helpers for the reports app.
Provides cache key builders and invalidation utilities
to keep hot-path queries fast.
"""
from __future__ import annotations

import logging
from typing import Optional

from django.core.cache import cache

logger = logging.getLogger(__name__)

# ── Default TTLs (seconds) ──────────────────────────────────────────
CACHE_TTL_SHORT = 60           # 1 minute  (notification counts, etc.)
CACHE_TTL_MEDIUM = 300         # 5 minutes (dashboard stats)
CACHE_TTL_LONG = 900           # 15 minutes (report-type lists, department lists)


# ── Key builders ────────────────────────────────────────────────────
def _k(prefix: str, *parts) -> str:
    """Build a namespaced cache key."""
    return ":".join(str(p) for p in (prefix, *parts))


def key_school_stats(school_id: int) -> str:
    return _k("school_stats", school_id)


def key_department_list(school_id: int) -> str:
    return _k("dept_list", school_id)


def key_reporttype_list(school_id: int) -> str:
    return _k("rtype_list", school_id)


def key_unread_count(user_id: int) -> str:
    return _k("unread", user_id)


def key_teacher_count(school_id: int) -> str:
    return _k("teacher_cnt", school_id)


# ── Invalidation helpers ────────────────────────────────────────────
def invalidate_school(school_id: int) -> None:
    """Bust all caches related to a specific school."""
    keys = [
        key_school_stats(school_id),
        key_department_list(school_id),
        key_reporttype_list(school_id),
        key_teacher_count(school_id),
    ]
    try:
        cache.delete_many(keys)
    except Exception:
        logger.debug("cache.delete_many failed for school %s", school_id)


def invalidate_user_notifications(user_id: int) -> None:
    """Bust notification count cache for a user."""
    try:
        cache.delete(key_unread_count(user_id))
    except Exception:
        pass


# ── Cached getters ──────────────────────────────────────────────────
def get_or_set(key: str, callback, ttl: int = CACHE_TTL_MEDIUM):
    """
    Safe wrapper around cache.get_or_set that falls back to the
    callback on any cache backend error.
    """
    try:
        val = cache.get(key)
        if val is not None:
            return val
    except Exception:
        pass
    val = callback()
    try:
        cache.set(key, val, ttl)
    except Exception:
        pass
    return val
