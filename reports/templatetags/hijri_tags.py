# reports/templatetags/hijri_tags.py
# -*- coding: utf-8 -*-
"""مرشّحات عرض التواريخ بالهجري في القوالب.

الاستخدام:
    {% load hijri_tags %}
    {{ report.report_date|hijri }}        → 1447/11/27
    {{ report.report_date|hijri_long }}   → 27 ذو القعدة 1447 هـ
    {{ report.report_date|hijri_day }}    → الأربعاء
"""
from django import template

from ..hijri_utils import hijri_date, hijri_long, hijri_day_name

register = template.Library()


@register.filter(name="hijri")
def hijri(value, fallback="—"):
    return hijri_date(value, fallback=fallback)


@register.filter(name="hijri_long")
def hijri_long_filter(value, fallback="—"):
    return hijri_long(value, fallback=fallback)


@register.filter(name="hijri_day")
def hijri_day(value, fallback=""):
    return hijri_day_name(value, fallback=fallback)
