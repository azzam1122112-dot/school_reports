# reports/views/report_templates.py
# -*- coding: utf-8 -*-
"""إدارة قوالب التقارير الجاهزة (مدير المدرسة) + مزوّد بيانات للمعلمين."""
from __future__ import annotations

from django.http import JsonResponse

from ._helpers import *
from ._helpers import _get_active_school, _user_manager_schools

from ..models import ReportTemplate
from ..forms import ReportTemplateForm


def _ensure_manager_school(request: HttpRequest):
    """تتحقق من اختيار مدرسة نشطة وصلاحية المدير عليها.

    تعيد (active_school, redirect_response). إن كان redirect_response غير None فالمطلوب إعادة التوجيه.
    """
    active_school = _get_active_school(request)
    if School.objects.filter(is_active=True).exists():
        if active_school is None:
            messages.error(request, "فضلاً اختر مدرسة أولاً.")
            return active_school, redirect("reports:select_school")
        if (not request.user.is_superuser) and active_school not in _user_manager_schools(request.user):
            messages.error(request, "ليست لديك صلاحية على هذه المدرسة.")
            return active_school, redirect("reports:select_school")
    return active_school, None


def active_templates_for_school(active_school) -> list[dict]:
    """قوالب نشطة للمدرسة، جاهزة كـ JSON لواجهة المعلم."""
    qs = ReportTemplate.objects.filter(is_active=True).select_related("category").order_by("order", "name")
    if active_school is not None and hasattr(ReportTemplate, "school"):
        qs = qs.filter(school=active_school)
    payload = []
    for t in qs:
        payload.append(
            {
                "id": t.id,
                "name": t.name,
                "title": t.title or "",
                "idea": t.idea or "",
                "beneficiaries_count": t.beneficiaries_count,
                "category_code": getattr(t.category, "code", "") or "",
                "category_name": getattr(t.category, "name", "") or "",
            }
        )
    return payload


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET"])
def report_templates_list(request: HttpRequest) -> HttpResponse:
    active_school, redirect_resp = _ensure_manager_school(request)
    if redirect_resp is not None:
        return redirect_resp

    qs = ReportTemplate.objects.select_related("category").order_by("order", "name")
    if active_school is not None and hasattr(ReportTemplate, "school"):
        qs = qs.filter(school=active_school)

    items = list(qs)
    return render(request, "reports/report_templates_list.html", {"items": items})


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def report_template_create(request: HttpRequest) -> HttpResponse:
    active_school, redirect_resp = _ensure_manager_school(request)
    if redirect_resp is not None:
        return redirect_resp

    form = ReportTemplateForm(request.POST or None, active_school=active_school)
    if request.method == "POST" and form.is_valid():
        obj = form.save(commit=False)
        if hasattr(obj, "school") and active_school is not None:
            obj.school = active_school
        obj.created_by = request.user
        obj.save()
        messages.success(request, "✅ تم إضافة القالب.")
        return redirect("reports:report_templates_list")
    if request.method == "POST":
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/report_template_form.html", {"form": form, "mode": "create"})


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["GET", "POST"])
def report_template_update(request: HttpRequest, pk: int) -> HttpResponse:
    active_school, redirect_resp = _ensure_manager_school(request)
    if redirect_resp is not None:
        return redirect_resp

    obj = get_object_or_404(ReportTemplate, pk=pk, school=active_school)
    form = ReportTemplateForm(request.POST or None, instance=obj, active_school=active_school)
    if request.method == "POST" and form.is_valid():
        form.save()
        messages.success(request, "✏️ تم تعديل القالب.")
        return redirect("reports:report_templates_list")
    if request.method == "POST":
        messages.error(request, "تعذّر الحفظ. تحقّق من الحقول.")
    return render(request, "reports/report_template_form.html", {"form": form, "mode": "edit", "obj": obj})


@login_required(login_url="reports:login")
@role_required({"manager"})
@require_http_methods(["POST"])
def report_template_delete(request: HttpRequest, pk: int) -> HttpResponse:
    active_school, redirect_resp = _ensure_manager_school(request)
    if redirect_resp is not None:
        return redirect_resp

    obj = get_object_or_404(ReportTemplate, pk=pk, school=active_school)
    name = obj.name
    try:
        obj.delete()
        messages.success(request, f"🗑️ تم حذف «{name}».")
    except Exception:
        logger.exception("report_template_delete failed")
        messages.error(request, "تعذّر حذف القالب.")
    return redirect("reports:report_templates_list")


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def api_report_templates(request: HttpRequest) -> JsonResponse:
    """قوالب التقارير النشطة للمدرسة النشطة (يستخدمها نموذج إضافة التقرير)."""
    active_school = _get_active_school(request)
    return JsonResponse({"templates": active_templates_for_school(active_school)})
