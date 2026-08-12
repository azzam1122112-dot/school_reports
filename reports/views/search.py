# reports/views/search.py
# -*- coding: utf-8 -*-
"""نقطة البحث الموحّد.

العرض هنا **رقيقٌ عمداً**: كل قرار رؤية في ``reports/search.py`` حيث يمكن
اختباره وحده، ولا يُعاد بناؤه في طبقة HTTP. وما يفعله هذا الملف ثلاثة أشياء
لا رابع: يقرأ الاستعلام، ويحدّ معدّل النداء، ويمنع الفهرسة.

**لماذا الحدّ.** الصندوق يُنادى مع كل ضغطة مفتاح، وكل نداء يمرّ على سبعة
جداول. فبلا حدٍّ يصير أرخص وسيلة لإشغال قاعدة البيانات متاحةً لكل حساب.

**ولماذا منع الفهرسة.** نتيجةُ البحث محتوى خاصٌّ بمن سأل، ووسمُ ``noindex``
يمنع تسرّبه إلى محرّكات البحث عبر أي رابط يُشارَك سهواً.
"""
from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, JsonResponse
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..search import MIN_QUERY_LENGTH, search
from ._helpers import _get_active_school


@login_required(login_url="reports:login")
@ratelimit(key="user", rate="60/m", method="GET", block=True)
@require_http_methods(["GET"])
def global_search(request: HttpRequest) -> JsonResponse:
    """نتائج البحث للمستخدم في مدرسته النشطة، بصيغة JSON."""
    query = (request.GET.get("q") or "").strip()
    active_school = _get_active_school(request)

    hits = search(request.user, active_school, query)

    response = JsonResponse(
        {
            "query": query,
            "min_length": MIN_QUERY_LENGTH,
            "results": [hit.as_dict() for hit in hits],
        },
        json_dumps_params={"ensure_ascii": False},
    )
    # محتوى خاصٌّ بصاحبه: لا يُفهرَس ولا يُخزَّن في كاش مشترك.
    response["X-Robots-Tag"] = "noindex, nofollow"
    response["Cache-Control"] = "private, no-store"
    return response
