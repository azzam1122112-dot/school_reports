# core/compromised_secrets.py
# -*- coding: utf-8 -*-
"""بصمات الأسرار التي دخلت تاريخ Git — تجزئات لا قيم.

**لماذا يوجد هذا الملف؟** لأن ``.env`` كان متتبَّعاً في هذا المستودع عبر 19
التزاماً قبل أن يُزال. والإزالة من التتبع لا تحذف المحتوى من التاريخ: كل نسخة
سابقة ما زالت تُستخرج بأمر واحد، وفيها مفتاح توقيع إنتاج وسلسلة اتصال بقاعدة
البيانات ومفاتيح تخزين. ومفتاح توقيع Django المسرَّب يعني تزوير جلسة أي
مستخدم — بما فيهم مالك النظام — دون كلمة مرور.

**ولماذا تجزئات؟** لأن كتابة القيم هنا تعيد نشر ما نحاول إبطاله. ``SHA-256``
تكفي للمقارنة ولا تُعيد المفتاح، فيبقى هذا الملف آمناً في مستودع عام لو صار
عاماً يوماً.

**ولماذا في الكود لا في وثيقة؟** لأن الوثيقة تُقرأ مرة وتُنسى، وهذا يُقرأ في كل
نشر: ``production_preflight`` يفشل إن كان السرّ الجاري ما زال أحد هذه القيم،
فيوقف الإصدار بدل أن يمرّ بصمت. توصيةٌ في تقرير تعتمد على أن يتذكرها أحد؛ فحصٌ
في مسار النشر لا يعتمد على أحد.

**الصيانة:** عند تدوير سرٍّ مكشوف، أضِف تجزئة القيمة القديمة هنا — لا تحذفها.
القائمة سجلٌّ تراكمي لما لا يجوز أن يعود، والحذف منها يفتح باباً أُغلق.

    python -c "import hashlib,sys; print(hashlib.sha256(sys.argv[1].encode()).hexdigest())" '<old-value>'
"""
from __future__ import annotations

import hashlib

__all__ = ["is_compromised", "compromised_names", "fingerprint"]


# مستخرَجة آلياً من ``git log --all -- .env`` بتاريخ 2026-08-11.
_COMPROMISED: dict[str, frozenset[str]] = {
    "SECRET_KEY": frozenset({
        "03e887c58d0c9c809be5d5eb61e5ea043d4b0466333feba16dab7944ca528fa8",
        "5e2143e108526358a65fcd23e559f636bd60301dde2c46121fecc4375b80f584",
        "bd4b969ec202e3b67240d985aaa4262999c614e4e7a2ccb7fcd314853a8a1d7b",
        "cf6898176dc6f7c02036ddcf54c55153a646387a571b8aedb94ba2933a5be92f",
    }),
    "DATABASE_URL": frozenset({
        "6247521113eab447a964c8f5f6fd40adabf09202a18c7adc6fb9dd4e81c2eac8",
        "85203c74fa411d32b8f88884ff41251c27876ce2b967811db97fcdd290910002",
        "fbf45bbf7f83d6fc1c918971483c6bcd087d33d7df8852338066794d3c022034",
    }),
    "CLOUDINARY_API_KEY": frozenset({
        "305d2ca4f0c28418f37b73c8cde24cfad3b2753e08c88f6c2df74a4c4f5bdc34",
        "aed091933f99df57f1ddd487f0f52cec7e744313cd1f0720700ad4e00a761a66",
        "cd7f8227036c211e453c87659e00d36da75667b6bc17c6ac3b9136884412d188",
    }),
    "CLOUDINARY_API_SECRET": frozenset({
        "57f164f9c5b151d6dc6122e375e0264f953c402d19b2e6b2cecd1f443a4db2f6",
        "759694f776e707181a34609f2eb7560caa413c64160d454f43dd8bdcd46d9de5",
        "ad9f8d665de889d4b10fa6d477d5a51f1f22e6cfa3eeb2caf6ae00bfd2af8759",
    }),
}


def fingerprint(value: str) -> str:
    """بصمة قابلة للمقارنة وغير قابلة للعكس."""
    return hashlib.sha256(str(value or "").strip().encode("utf-8")).hexdigest()


def is_compromised(name: str, value: str) -> bool:
    """هل هذه القيمة بعينها ظهرت في تاريخ Git تحت هذا الاسم؟"""
    if not value:
        return False
    return fingerprint(value) in _COMPROMISED.get(str(name or "").upper(), frozenset())


def compromised_names() -> tuple[str, ...]:
    """أسماء الإعدادات التي لها بصمات مسجَّلة — لدوران الفحص."""
    return tuple(sorted(_COMPROMISED))
