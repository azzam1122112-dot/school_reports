# -*- coding: utf-8 -*-
"""أدوات إدخال مشتركة بين النماذج.

**لماذا وحدةٌ لحقل واحد؟** لأن الحقل الذي يُكتب بيدٍ في كل نموذج يُنسى في
أحدها. حقل ``datetime-local`` مثالٌ حيّ: صيغته كُتبت صحيحةً في نموذج التفويض
ونُسيت في التكليف والاجتماع والخطة والتعميم، فبقي المدير يكتب التاريخ يدوياً
في شاشتين إلزاميتين بينما الكود يظنّ أنه ملأهما له. الحقل هنا يجعل الصواب هو
السلوك الافتراضي، فلا يحتاج من يضيف نموذجاً جديداً أن يعرف الحكاية.
"""
from __future__ import annotations

from django import forms

__all__ = ["DATETIME_LOCAL_FORMAT", "DateTimeLocalInput"]

# الصيغة التي تنصّ عليها مواصفة HTML لقيمة ``datetime-local``.
DATETIME_LOCAL_FORMAT = "%Y-%m-%dT%H:%M"


class DateTimeLocalInput(forms.DateTimeInput):
    """حقل تاريخ ووقت بالصيغة التي يقرأها المتصفح فعلاً.

    ``forms.DateTimeInput`` يطبع القيمة الابتدائية بصيغة جانغو العامة
    (``2026-09-03 15:18:17`` بمسافة)، ومواصفة HTML لا تقبل في
    ``datetime-local`` إلا ``2026-09-03T15:18`` بحرف ``T``. فالمتصفح يرفض
    القيمة **صامتاً ويعرض حقلاً فارغاً**: لا خطأ في السجلّ، ولا شيء يفشل في
    الاختبارات لأن الإرسال يعمل — وحده المستخدم يرى خانةً إلزاميةً فارغة حيث
    وضع الكود له موعداً افتراضياً.

    الإرسال في الاتجاه المعاكس سليم أصلاً: ``forms.DateTimeField.to_python``
    يمرّ على ``parse_datetime`` أولاً وهي تفهم ISO 8601 بحرف ``T``.
    """

    def __init__(self, attrs=None, format=None):  # noqa: A002 — الاسم من جانغو
        merged = {"type": "datetime-local"}
        if attrs:
            merged.update(attrs)
        super().__init__(attrs=merged, format=format or DATETIME_LOCAL_FORMAT)
