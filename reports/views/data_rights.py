# reports/views/data_rights.py
# -*- coding: utf-8 -*-
"""شاشة «بياناتي»: النسخة المقروءة وطلب الإتلاف.

سياسة الخصوصية تَعِد بحقوق نظام حماية البيانات الشخصية، وكان الوفاء بها يمرّ
بنموذج شكاوى ومعالجة يدوية. هذه الشاشة تجعل حق الوصول فورياً، وتجعل طلب
الإتلاف مسجَّلاً ومُتتبَّعاً بدل بريدٍ قد لا يُردّ عليه.

**النطاق مثبَّت في الكود لا في الطلب.** كل استعلام هنا يبدأ من ``request.user``؛
فليس في المسار معامل يمكن التلاعب به لتنزيل نسخة شخصٍ آخر. راجع
``reports/tests/test_data_rights.py`` حيث يُفحص ذلك صراحةً.
"""
from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..models import ErasureRequest
from ..services_data_rights import build_personal_data_export


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_data(request: HttpRequest) -> HttpResponse:
    """صفحة الحقوق: ما نحتفظ به، وكيف تأخذ نسخة، وكيف تطلب الإتلاف."""
    open_request = (
        ErasureRequest.objects.filter(teacher=request.user)
        .order_by("-created_at", "-id")
        .first()
    )
    return render(
        request,
        "reports/my_data.html",
        {"active": "my_data", "erasure_request": open_request},
    )


@login_required(login_url="reports:login")
# بناء النسخة يمرّ على أحد عشر جدولاً. حدٌّ منخفض عمداً: الحق فوري، لكنه ليس
# وسيلةً لإشغال القاعدة.
@ratelimit(key="user", rate="5/h", method="GET", block=True)
@require_http_methods(["GET"])
def my_data_download(request: HttpRequest) -> HttpResponse:
    """تنزيل نسخة البيانات الشخصية بصيغة JSON."""
    payload = build_personal_data_export(request.user)
    body = json.dumps(payload, ensure_ascii=False, indent=2)

    stamp = timezone.now().strftime("%Y%m%d")
    response = HttpResponse(body, content_type="application/json; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="tawtheeq-my-data-{stamp}.json"'
    # نسخةُ بياناتٍ شخصية: لا تُخزَّن في أي كاش مشترك ولا تُفهرَس.
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


@login_required(login_url="reports:login")
@ratelimit(key="user", rate="5/h", method="POST", block=True)
@require_http_methods(["POST"])
def request_erasure(request: HttpRequest) -> HttpResponse:
    """تسجيل طلب إتلاف بيانات.

    الطلب المفتوح واحدٌ لكل شخص — يحرسه قيدٌ في القاعدة — فإعادةُ الإرسال
    تُطمئن ولا تُنشئ صفاً ثانياً يُشتّت المعالجة.
    """
    existing = ErasureRequest.objects.filter(
        teacher=request.user,
        status__in=[ErasureRequest.Status.RECEIVED, ErasureRequest.Status.IN_REVIEW],
    ).first()

    if existing:
        messages.info(
            request,
            "لديك طلب إتلاف قائم قيد المعالجة. سنبلغك بالنتيجة ضمن المدد النظامية.",
        )
        return redirect("reports:my_data")

    ErasureRequest.objects.create(
        teacher=request.user,
        reason=(request.POST.get("reason") or "").strip()[:2000],
    )
    messages.success(
        request,
        "سُجّل طلب الإتلاف. سنراجعه ونبلغك بالنتيجة ضمن المدد النظامية، وقد "
        "يتعذّر إتلاف ما يوجب النظام الاحتفاظ به.",
    )
    return redirect("reports:my_data")
