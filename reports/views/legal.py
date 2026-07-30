from __future__ import annotations

from django.contrib import messages
from django.http import HttpRequest, HttpResponse
from django.shortcuts import redirect, render
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..customer_care_forms import CustomerComplaintForm


def terms_conditions(request: HttpRequest) -> HttpResponse:
    return render(request, "reports/terms_conditions.html")


def refund_policy(request: HttpRequest) -> HttpResponse:
    return render(request, "reports/refund_policy.html")


def service_delivery_policy(request: HttpRequest) -> HttpResponse:
    return render(request, "reports/service_delivery_policy.html")


@ratelimit(key="ip", rate="5/h", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def complaints_policy(request: HttpRequest) -> HttpResponse:
    form = CustomerComplaintForm(request.POST or None)
    submitted_reference = ""
    if request.method == "POST" and form.is_valid():
        complaint = form.save()
        submitted_reference = complaint.reference
        messages.success(
            request,
            f"استلمنا شكواك بنجاح. رقم المتابعة: {submitted_reference}",
        )
        return redirect(f"{request.path}?submitted={submitted_reference}")

    if request.method == "GET":
        submitted_reference = (request.GET.get("submitted") or "").strip()[:40]

    return render(
        request,
        "reports/complaints_policy.html",
        {"complaint_form": form, "submitted_reference": submitted_reference},
    )

