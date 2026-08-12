# reports/views/api_keys.py
# -*- coding: utf-8 -*-
"""شاشة مفاتيح التكامل — لمدير المدرسة وحده.

**لماذا المدير وحده.** المفتاح يعمل بصلاحيات شخص، ومنحُ ذلك لمن دونه يجعل
إصدار المفاتيح طريقاً لتصعيد الامتيازات: يُنشئ الموظف مفتاحاً «يعمل بصلاحيات»
غيره فيتجاوز نطاقه. فالإصدار محصورٌ بمن يملك المدرسة كلها أصلاً.

**والسرّ يُعرض مرة واحدة.** يُمرَّر عبر رسالة الجلسة لا عبر القاعدة، فلا يبقى
مخزَّناً في أي مكان بعد عرضه. ومن فقده أنشأ غيره وأبطل القديم — وهذا هو الفرق
بين تسريبٍ يُحتوى وتسريبٍ يُكتشف بعد شهور.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..model_parts.api_keys import generate_api_key
from ..models import SchoolApiKey, SchoolMembership
from ._helpers import _get_active_school, _user_manager_schools

# مفتاحٌ جديد يُمرَّر في الجلسة لعرضةٍ واحدة ثم يُمسح.
_NEW_KEY_SESSION = "_new_api_key_once"


def _manager_school_or_redirect(request: HttpRequest):
    school = _get_active_school(request)
    if school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return None, redirect("reports:select_school")
    if not request.user.is_superuser and school not in _user_manager_schools(request.user):
        messages.error(request, "مفاتيح التكامل متاحة لمدير المدرسة وحده.")
        return None, redirect("reports:home")
    return school, None


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def api_keys_list(request: HttpRequest) -> HttpResponse:
    school, redirect_response = _manager_school_or_redirect(request)
    if redirect_response is not None:
        return redirect_response

    keys = (
        SchoolApiKey.objects.filter(school=school)
        .select_related("acting_as")
        .order_by("-created_at")
    )
    return render(
        request,
        "reports/api_keys.html",
        {
            "active": "api_keys",
            "active_school": school,
            "api_keys": keys,
            # يُقرأ مرة ثم يُمسح: العرض الثاني للصفحة لا يُظهره.
            "new_key": request.session.pop(_NEW_KEY_SESSION, None),
            "staff": SchoolMembership.objects.filter(
                school=school,
                is_active=True,
                role_type__in=SchoolMembership.STAFF_ROLES,
            ).select_related("teacher").order_by("teacher__name"),
        },
    )


@login_required(login_url="reports:login")
@ratelimit(key="user", rate="20/h", method="POST", block=True)
@require_http_methods(["POST"])
def api_key_create(request: HttpRequest) -> HttpResponse:
    school, redirect_response = _manager_school_or_redirect(request)
    if redirect_response is not None:
        return redirect_response

    name = (request.POST.get("name") or "").strip()[:120]
    scope = (request.POST.get("scope") or SchoolApiKey.Scope.READ).strip()
    acting_as_id = (request.POST.get("acting_as") or "").strip()

    if not name:
        messages.error(request, "اكتب اسماً يوضّح لأي نظام هذا المفتاح.")
        return redirect("reports:api_keys")
    if scope not in SchoolApiKey.Scope.values:
        scope = SchoolApiKey.Scope.READ

    # **الهوية المرتبطة يجب أن تكون منسوباً نشطاً في هذه المدرسة.** بلا هذا
    # الفحص يصير حقلٌ في نموذج HTML طريقاً لإصدار مفتاح يعمل بصلاحيات شخصٍ في
    # مدرسة أخرى.
    membership = SchoolMembership.objects.filter(
        school=school,
        teacher_id=acting_as_id or None,
        is_active=True,
        role_type__in=SchoolMembership.STAFF_ROLES,
    ).select_related("teacher").first()
    if membership is None:
        messages.error(request, "اختر منسوباً نشطاً في هذه المدرسة ليعمل المفتاح بصلاحياته.")
        return redirect("reports:api_keys")

    raw_key, public_id, key_hash = generate_api_key()
    SchoolApiKey.objects.create(
        school=school,
        name=name,
        public_id=public_id,
        key_hash=key_hash,
        scope=scope,
        acting_as=membership.teacher,
        created_by=request.user,
    )

    request.session[_NEW_KEY_SESSION] = raw_key
    messages.success(request, "أُنشئ المفتاح. انسخه الآن — لن يُعرض مرة أخرى.")
    return redirect("reports:api_keys")


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def api_key_revoke(request: HttpRequest, pk: int) -> HttpResponse:
    school, redirect_response = _manager_school_or_redirect(request)
    if redirect_response is not None:
        return redirect_response

    # ``school=school`` في الجلب لا بعده: مديرُ مدرسةٍ لا يُبطل مفتاح أخرى برقمه.
    key = get_object_or_404(SchoolApiKey, pk=pk, school=school)
    key.is_active = False
    key.save(update_fields=["is_active"])

    messages.success(request, f"أُبطل المفتاح «{key.name}». أي طلب يستعمله سيُرفض فوراً.")
    return redirect("reports:api_keys")
