# core/observability.py
# -*- coding: utf-8 -*-
"""تدهورٌ مُعلَن بدل عطلٍ صامت.

المنصة مليئة بمواضع تختار عمداً ألا تسقط: قسمٌ في اللوحة يتعذّر بناؤه، عدّادٌ
يفشل استعلامه، ترويسةٌ لا تُقرأ. والاختيار صحيح — صفحةٌ ناقصة أهون من صفحة
معطّلة. لكن ``except Exception: pass`` يشتري ذلك بثمنٍ لم يُحسب: **الفشل لا
يُترك للعِيان**.

فالنتيجة أن قسماً فارغاً لا يمكن تمييزه عن قسمٍ لا بيانات له، لا في الشاشة ولا
في Sentry ولا في السجل. والعميل يبلّغ «القسم فارغ» فيبدأ التشخيص من الصفر في كل
مرة، لأن اللحظة التي كانت تحمل الجواب مرّت ولم تُسجَّل.

وهذه الوحدة تُبقي القرار — لا نُسقط الصفحة — وتُلغي ثمنه:

* كل ابتلاع **مُسمّى** بمعرّف مستقر (``nav.hero_notification``) يصلح للبحث
  والتنبيه وعدّ التكرار عبر الإصدارات.
* كل ابتلاع **يُسجَّل** بأثر الاستثناء كاملاً، فيصل Sentry ويصل السجل.
* كل ابتلاع **يُعدّ** في ``opmetrics``، فيُقاس معدّل التدهور بدل أن يُكتشف من
  بلاغ عميل.
* والمُستدعي يعرف أن العملية تعثّرت (``degraded``) فيعرض «تعذّر التحميل» بدل
  فراغٍ يُقرأ «لا يوجد».

الاستعمال::

    from core.observability import soft_call, soft_fail

    # قيمة بديلة عند التعثّر
    count = soft_call("nav.unread_count", lambda: qs.count(), default=0, user_id=user.pk)

    # كتلة جُمَلٍ يجوز أن تتعثّر
    with soft_fail("nav.hero_notification", user_id=user.pk) as outcome:
        ctx["hero"] = build_hero(user)
    if outcome.failed:
        ctx["hero_degraded"] = True

القاعدة الحاكمة: **لا ``except Exception: pass`` جديد.** إن كان التعثّر مقبولاً
فمكانه هنا باسم؛ وإن لم يكن مقبولاً فالاستثناء يُرفع.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Callable, Iterator, TypeVar

logger = logging.getLogger("tawtheeq.degraded")

T = TypeVar("T")

# بادئة عدّادات التدهور في ``opmetrics``. مقصودة الثبات: التنبيهات تُبنى عليها.
_METRIC_PREFIX = "degraded"


def _count(operation: str) -> None:
    """يعدّ التعثّر دون أن يصير العدّ نفسه سبباً لسقوط الطلب."""
    try:
        from core import opmetrics

        opmetrics.increment(f"{_METRIC_PREFIX}:{operation}")
        opmetrics.increment(f"{_METRIC_PREFIX}:_total")
    except Exception:  # noqa: BLE001 - عدّاد المراقبة آخر ما يُسمح له بالإسقاط
        logger.debug("degradation counter unavailable for %s", operation, exc_info=True)


def _report(operation: str, context: dict[str, Any]) -> None:
    """يسجّل التعثّر بأثره الكامل ثم يعدّه."""
    if context:
        logger.exception(
            "تعثّرت العملية %s — أُكملت بقيمة بديلة (%s)",
            operation,
            ", ".join(f"{key}={value!r}" for key, value in sorted(context.items())),
        )
    else:
        logger.exception("تعثّرت العملية %s — أُكملت بقيمة بديلة", operation)
    _count(operation)


def report_degraded(operation: str, **context: Any) -> None:
    """يسجّل تعثّراً جارياً داخل ``except`` قائم، ثم يترك المعالجة للمُستدعي.

    لكتل ``except`` التي تفعل أكثر من إعادة قيمة — تُعيد مساراً بديلاً، أو
    تُكمل حلقة، أو تُصفّر حالة. يُستدعى **داخل** الكتلة ليلتقط
    ``logger.exception`` الأثرَ الجاري.
    """
    _report(operation, context)


@dataclass
class Outcome:
    """نتيجة كتلة ``soft_fail`` — يقرؤها المُستدعي ليميّز الفراغ من العطل.

    **الاستثناء يُحتفظ به بلا أثره (traceback).** الأثر يمسك الإطارات، والإطارات
    تمسك متغيّراتها المحلية — فكائنٌ يعيش بعد الكتلة ويحمل أثراً كاملاً يُبقي
    استعلاماً أو ملفاً مرفوعاً في الذاكرة إلى أن يُجمع. وهو كذلك غير قابل
    للتسلسل (``pickle``)، فيكسر كل ما يمرّ به بين العمليات — من كاش إلى مُشغِّل
    اختبارات متوازٍ.

    والأثر لم يُفقد: ``logger.exception`` سجّله كاملاً لحظة وقوعه. وما يبقى هنا
    هو ما يحتاجه المُستدعي فعلاً — النوع والرسالة.
    """

    operation: str
    failed: bool = False
    error: BaseException | None = field(default=None, repr=False)

    @property
    def ok(self) -> bool:
        return not self.failed

    @property
    def error_type(self) -> str:
        return type(self.error).__name__ if self.error is not None else ""


@contextmanager
def soft_fail(operation: str, **context: Any) -> Iterator[Outcome]:
    """ينفّذ الكتلة، وإن تعثّرت سجّلها وعدّها وأكمل.

    ``operation`` معرّف مستقر بصيغة ``نطاق.عملية`` — يُبحث به في Sentry ويُبنى
    عليه التنبيه، فلا يُغيَّر بلا سبب.

    القيمة المُنتَجة كائن ``Outcome`` يقرؤه المُستدعي بعد الكتلة::

        with soft_fail("dashboard.recent_activity", school_id=school.pk) as outcome:
            ctx["activities"] = build_activities(school)
        ctx["activities_degraded"] = outcome.failed
    """
    outcome = Outcome(operation=operation)
    try:
        yield outcome
    except Exception as exc:  # noqa: BLE001 - الابتلاع مقصود، والإعلان عنه هو الغرض
        _report(operation, context)
        outcome.failed = True
        # يُجرَّد من أثره بعد تسجيله — راجع تعليل ``Outcome``.
        outcome.error = exc.with_traceback(None)


def soft_call(
    operation: str,
    func: Callable[[], T],
    *,
    default: T = None,  # type: ignore[assignment]
    **context: Any,
) -> T:
    """يُعيد ``func()``، وعند التعثّر يسجّله ويُعيد ``default``.

    للحالات التي كانت ``try: return X / except: return Y`` — والفرق أن ``Y``
    الآن مصحوبةٌ بسطر سجلّ يقول لماذا.
    """
    try:
        return func()
    except Exception:  # noqa: BLE001 - الابتلاع مقصود، والإعلان عنه هو الغرض
        _report(operation, context)
        return default


def soft(operation: str, *, default: Any = None) -> Callable:
    """مُزخرِف للدوال المساعدة التي عقدها «أعطِ قيمة أو بديلاً، ولا تُسقط».

    مثال::

        @soft("nav.count", default=0)
        def _safe_count(qs) -> int:
            return qs.count()
    """

    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        from functools import wraps

        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            try:
                return func(*args, **kwargs)
            except Exception:  # noqa: BLE001 - الابتلاع مقصود، والإعلان عنه هو الغرض
                _report(operation, {})
                return default

        return wrapper

    return decorator


__all__ = ["Outcome", "report_degraded", "soft", "soft_call", "soft_fail"]
