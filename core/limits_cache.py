# core/limits_cache.py
# -*- coding: utf-8 -*-
"""مخزن عدّادات الحدود — منفصل عن كاش العرض متى تهيّأ.

**عدّاد الحدّ ليس كاشاً.** الكاش يُعاد بناؤه عند الضياع فلا يضرّ ضياعه إلا
بالسرعة؛ أما العدّاد الضائع فيُقرأ «صفر محاولات» — أي أن ضياعه يُلغي الحماية
لا يُبطئها. والفرق يظهر عملياً في أن كاش العرض مضبوط عمداً على
``volatile-lru`` مع ``IGNORE_EXCEPTIONS: True``: إخلاءٌ صامت تحت الضغط،
واستثناءٌ مبتلع. وهما معاً يمحوان حدود الدخول وميزانيات المستأجر وسقف فاتورة
الذكاء الاصطناعي **في لحظة الذروة**، وهي اللحظة التي وُجدت لأجلها.

يُقرأ الاسم من ``settings.RATELIMIT_USE_CACHE`` كي يبقى مصدر الحقيقة واحداً مع
``django-ratelimit``: مخزنان مختلفان للعدّاد نفسه يعنيان حدَّين لا حدّاً.

والسقوط إلى ``default`` مقصود: بيئة لم تُفصل بعد تظل عاملة، والفصل ترقية
تشغيلية لا شرط تشغيل.
"""
from __future__ import annotations

from django.conf import settings
from django.core.cache import InvalidCacheBackendError, caches

__all__ = ["limits_cache", "limits_cache_is_isolated"]


def _configured_alias() -> str:
    return str(getattr(settings, "RATELIMIT_USE_CACHE", "") or "").strip() or "limits"


def limits_cache():
    """الكاش المخصص للعدّادات، أو الافتراضي إن لم يُهيَّأ بعد."""
    try:
        return caches[_configured_alias()]
    except InvalidCacheBackendError:
        return caches["default"]


def limits_cache_is_isolated() -> bool:
    """هل العدّادات على مخزن مستقل فعلاً؟

    يقرؤها فحص ما قبل الإنتاج: تشغيل ``LOGIN_THROTTLE_FAIL_CLOSED`` على مخزن
    مشترك قابل للإخلاء يحوّل طبقة تشديد إلى سبب تعطّل.
    """
    alias = _configured_alias()
    try:
        caches[alias]
    except InvalidCacheBackendError:
        return False
    return alias != "default"
