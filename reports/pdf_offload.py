# reports/pdf_offload.py
# -*- coding: utf-8 -*-
"""تنفيذ توليد PDF في عامل الوسائط بدل عامل الويب.

**المشكلة.** WeasyPrint أثقل عملية في المنصة: يحوّل HTML إلى PDF فيقفز
المعالج والذاكرة معاً. وكان يعمل **بالتزامن داخل عامل الويب**، فبضعةُ طلبات
متزامنة تُشبع حاوية ``web`` (2 vCPU) وتُبطئ كل صفحة أخرى — بينما ``worker-media``
المخصَّص للعمل الثقيل يقف خاملاً.

**ما لم نفعله ولماذا.** الحل الشائع تخزينُ الناتج وخدمتُه لاحقاً. وهو هنا
**غير آمن**: ``AchievementSection`` بلا ``updated_at``، وحذفُ شاهدٍ لا يترك أثراً
زمنياً — فلا سبيل إلى إثبات أن نسخة مخزَّنة ما زالت مطابقة للمحتوى. وخدمةُ ملفٍ
قديم على أنه الحالي تغييرٌ في السلوك لا تحسينٌ في الأداء.

**ما فعلناه.** يُنفَّذ التوليد نفسه، بالمحتوى نفسه، في اللحظة نفسها — لكن في
عامل الوسائط. ينتظر الويبُ النتيجة فيبقى خيطه موقوفاً على انتظارٍ **بلا معالج**
بدل أن يحرق المعالج بنفسه، فتتحرّر الحاوية لبقية الطلبات.

والبايتات تعود عبر الذاكرة المؤقتة لا عبر ``result backend``: حمولة الأخير
تُسلسَل JSON (فتلزم base64 بزيادة الثلث) وتبقى ساعةً كاملة بحكم
``CELERY_RESULT_EXPIRES`` — وذلك ثقلٌ على Redis محدود الذاكرة. أما هنا فمفتاح
واحد قصير العمر يُحذف فور قراءته.

**والارتداد مضمون.** أي تعثّر — وسيط معطّل، عامل مشغول، مهلة، ذاكرة مؤقتة
ساقطة — يعود بالتوليد المحلي فوراً كما كان قبل هذا الملف. فالمكسب أداءٌ عند
توفّر البنية، لا اعتمادٌ عليها.
"""
from __future__ import annotations

import logging
import secrets
from typing import Callable, Optional

from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# عمر مفتاح النتيجة. يُحذف فور القراءة، وهذه المدة لِما لم يُقرأ لعطلٍ ما.
PDF_RESULT_TTL_SECONDS = int(getattr(settings, "PDF_RESULT_TTL_SECONDS", 180))


# المهلة والمفتاح يُقرآن **عند كل نداء** لا عند الاستيراد: مفتاحُ إيقاف لا
# يُطفأ إلا بإعادة نشرٍ ليس مفتاح إيقاف. ولو ساء التفريغ في الإنتاج وجب أن
# يكفي تغييرُ متغيّر بيئة وإعادةُ تشغيل الحاوية — لا بناءُ صورة جديدة.
def _offload_enabled() -> bool:
    return bool(getattr(settings, "PDF_OFFLOAD_ENABLED", True))


def _offload_timeout() -> float:
    # أقصر بكثير من ``CELERY_TASK_TIME_LIMIT`` عمداً: خيط ويب ينتظر عاملاً
    # متعثّراً نصفَ ساعة أسوأ من توليدٍ محلي يستغرق ثوانٍ.
    try:
        return float(getattr(settings, "PDF_OFFLOAD_TIMEOUT_SECONDS", 45))
    except (TypeError, ValueError):
        return 45.0


def _result_key() -> str:
    return f"pdf:render:{secrets.token_urlsafe(16)}"


def store_rendered_pdf(cache_key: str, pdf_bytes: bytes) -> None:
    """يضع الناتج في مفتاح قصير العمر ليقرأه الويب. يُستدعى من العامل."""
    cache.set(cache_key, pdf_bytes, PDF_RESULT_TTL_SECONDS)


def _take_rendered_pdf(cache_key: str) -> Optional[bytes]:
    """يقرأ الناتج ثم يحذف مفتاحه — قراءةٌ واحدة لا أثر بعدها."""
    try:
        payload = cache.get(cache_key)
    except Exception:
        return None
    finally:
        try:
            cache.delete(cache_key)
        except Exception:
            pass
    return payload if isinstance(payload, (bytes, bytearray)) else None


def render_pdf_offloaded(
    *,
    task,
    task_args: list,
    render_locally: Callable[[], bytes],
    label: str = "pdf",
) -> bytes:
    """يولّد PDF في عامل الوسائط، ويعود بالتوليد المحلي عند أي تعثّر.

    ``task`` مهمة Celery تنتهي باستدعاء :func:`store_rendered_pdf`، و
    ``task_args`` وسائطُها **دون** مفتاح النتيجة (يُضاف هنا). و``render_locally``
    هو المسار الذي كان قائماً قبل هذا الملف، ويبقى مرجعَ الصحة.
    """
    if not _offload_enabled():
        return render_locally()

    # في الاختبارات ``CELERY_TASK_ALWAYS_EAGER`` يجعل المهمة تعمل داخل العملية
    # نفسها، فلا فائدة من دورة الذاكرة المؤقتة — والتوليد المحلي أوضح وأسرع.
    if getattr(settings, "CELERY_TASK_ALWAYS_EAGER", False):
        return render_locally()

    cache_key = _result_key()
    try:
        async_result = task.apply_async(args=[*task_args, cache_key], queue="images")
        async_result.get(timeout=_offload_timeout(), propagate=True)
    except Exception:
        # لا نُخفي السبب: تعذّرُ التفريغ حدثٌ تشغيلي يستحق أن يُرى في السجل،
        # لأن تكرارَه يعني أن كل PDF يعود إلى حرق معالج الويب من حيث لا يُدرى.
        logger.warning(
            "PDF offload to the media worker failed for %s; rendering inline.",
            label,
            exc_info=True,
        )
        _take_rendered_pdf(cache_key)
        return render_locally()

    payload = _take_rendered_pdf(cache_key)
    if payload:
        return bytes(payload)

    # نجحت المهمة ولم تصل البايتات: ذاكرة مؤقتة ساقطة أو مفتاح انتهى. نولّد
    # محلياً بدل أن نُرجع ملفاً فارغاً.
    logger.warning("PDF offload produced no payload for %s; rendering inline.", label)
    return render_locally()
