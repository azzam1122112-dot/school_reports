# -*- coding: utf-8 -*-
"""Self-service additional-school creation for existing managers."""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required, user_passes_test
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods
from django_ratelimit.decorators import ratelimit

from ..forms import SchoolAdditionRequestForm
from ..models import (
    School,
    SchoolAdditionRequest,
    SchoolArchiveAddon,
    SchoolMembership,
    SchoolSubscription,
)
from ._helpers import _get_active_school
from .onboarding import (
    TRIAL_ARCHIVE_STORAGE_GB,
    _generate_unique_school_code,
    _get_or_create_trial_plan,
)


def _manager_schools(user):
    return School.objects.filter(
        is_active=True,
        memberships__teacher=user,
        memberships__role_type=SchoolMembership.RoleType.MANAGER,
        memberships__is_active=True,
    ).distinct().order_by("name", "id")


def _is_school_manager(user) -> bool:
    return bool(
        getattr(user, "is_authenticated", False)
        and SchoolMembership.objects.filter(
            teacher=user,
            role_type=SchoolMembership.RoleType.MANAGER,
            is_active=True,
        ).exists()
    )


def _provision_school_for_request(
    addition_request_id: int,
    *,
    reviewed_by=None,
    review_notes: str = "",
):
    """Create and link a school, then mark its audit request as approved.

    The caller must wrap this helper in ``transaction.atomic()`` so the school,
    manager membership, trial and request status are committed together.
    """
    locked_request = SchoolAdditionRequest.objects.select_for_update().get(
        pk=addition_request_id
    )
    if locked_request.status != SchoolAdditionRequest.Status.PENDING:
        raise ValidationError("تمت معالجة طلب إضافة المدرسة مسبقًا.")

    if School.objects.filter(
        name__iexact=locked_request.school_name,
        city__iexact=locked_request.city,
        is_active=True,
    ).exists():
        raise ValidationError(
            "توجد مدرسة نشطة بالاسم والمدينة نفسيهما. تواصل مع مدير المنصة إذا كانت المدرسة تخصك."
        )

    school = School.objects.create(
        name=locked_request.school_name,
        code=_generate_unique_school_code(locked_request.school_name),
        stage=locked_request.stage,
        gender=locked_request.gender,
        city=locked_request.city,
        phone=locked_request.phone or None,
        email=locked_request.email,
        is_active=True,
    )
    SchoolMembership.objects.create(
        school=school,
        teacher=locked_request.requested_by,
        role_type=SchoolMembership.RoleType.MANAGER,
        is_active=True,
    )
    trial_plan = _get_or_create_trial_plan()
    subscription = SchoolSubscription.objects.create(school=school, plan=trial_plan)
    SchoolArchiveAddon.objects.create(
        school=school,
        is_enabled=True,
        start_date=subscription.start_date,
        end_date=subscription.end_date,
        storage_limit_gb=max(1, TRIAL_ARCHIVE_STORAGE_GB),
        paid_amount=0,
        notes="مساحة أرشيف تجريبية لمدرسة إضافية أُنشئت تلقائيًا.",
    )
    locked_request.status = SchoolAdditionRequest.Status.APPROVED
    locked_request.created_school = school
    locked_request.reviewed_by = reviewed_by
    locked_request.reviewed_at = timezone.now()
    locked_request.review_notes = review_notes
    locked_request.save(
        update_fields=[
            "status",
            "created_school",
            "reviewed_by",
            "reviewed_at",
            "review_notes",
            "updated_at",
        ]
    )
    return locked_request, school


@login_required(login_url="reports:login")
@ratelimit(key="user", rate="10/h", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def school_addition_requests(request):
    """Let a current manager create another isolated school workspace."""
    if not _is_school_manager(request.user):
        messages.error(request, "هذه الخدمة متاحة لمدير المدرسة فقط.")
        return redirect("reports:home")

    if request.method == "POST":
        form = SchoolAdditionRequestForm(request.POST, requested_by=request.user)
        if form.is_valid():
            addition_request = form.save(commit=False)
            addition_request.requested_by = request.user
            active_school = _get_active_school(request)
            if active_school and _manager_schools(request.user).filter(pk=active_school.pk).exists():
                addition_request.source_school = active_school
            try:
                with transaction.atomic():
                    # Serialise simultaneous submissions from the same account.
                    request.user.__class__.objects.select_for_update().get(pk=request.user.pk)
                    addition_request.save()
                    _, school = _provision_school_for_request(
                        addition_request.pk,
                        review_notes="تم إنشاء المدرسة تلقائيًا من حساب المدير.",
                    )
            except ValidationError as exc:
                error_messages = getattr(exc, "messages", None)
                form.add_error(
                    "school_name",
                    " ".join(error_messages) if error_messages else str(exc),
                )
            else:
                messages.success(
                    request,
                    f"تم إنشاء {school.name} وربطها بحسابك وتفعيل الباقة التجريبية مباشرة.",
                )
                return redirect("reports:school_addition_requests")
    else:
        form = SchoolAdditionRequestForm(requested_by=request.user)

    requests = SchoolAdditionRequest.objects.filter(requested_by=request.user).select_related(
        "created_school", "reviewed_by"
    )
    return render(
        request,
        "reports/school_addition_requests.html",
        {
            "form": form,
            "addition_requests": requests,
        },
    )


@login_required(login_url="reports:platform_login")
@user_passes_test(lambda user: getattr(user, "is_superuser", False), login_url="reports:platform_login")
@require_http_methods(["GET"])
def platform_school_addition_requests(request):
    status = (request.GET.get("status") or SchoolAdditionRequest.Status.PENDING).strip()
    if status not in SchoolAdditionRequest.Status.values and status != "all":
        status = SchoolAdditionRequest.Status.PENDING
    query = (request.GET.get("q") or "").strip()
    scope = SchoolAdditionRequest.objects.select_related(
        "requested_by", "source_school", "created_school", "reviewed_by"
    )
    counts = {
        value: scope.filter(status=value).count()
        for value in SchoolAdditionRequest.Status.values
    }
    if status != "all":
        scope = scope.filter(status=status)
    if query:
        scope = scope.filter(
            Q(school_name__icontains=query)
            | Q(city__icontains=query)
            | Q(requested_by__name__icontains=query)
            | Q(requested_by__phone__icontains=query)
        )
    return render(
        request,
        "reports/platform_school_addition_requests.html",
        {"addition_requests": scope[:100], "current_status": status, "counts": counts, "query": query},
    )


@login_required(login_url="reports:platform_login")
@user_passes_test(lambda user: getattr(user, "is_superuser", False), login_url="reports:platform_login")
@require_http_methods(["POST"])
def platform_school_addition_request_review(request, pk: int):
    addition_request = get_object_or_404(
        SchoolAdditionRequest.objects.select_related("requested_by"), pk=pk
    )
    if addition_request.status != SchoolAdditionRequest.Status.PENDING:
        messages.warning(request, "تمت مراجعة هذا الطلب مسبقًا.")
        return redirect("reports:platform_school_addition_requests")

    action = (request.POST.get("action") or "").strip()
    review_notes = (request.POST.get("review_notes") or "").strip()[:1000]
    if action == "reject":
        if not review_notes:
            messages.error(request, "اكتب سبب الرفض ليظهر لمدير المدرسة.")
            return redirect("reports:platform_school_addition_requests")
        addition_request.status = SchoolAdditionRequest.Status.REJECTED
        addition_request.reviewed_by = request.user
        addition_request.reviewed_at = timezone.now()
        addition_request.review_notes = review_notes
        addition_request.save(update_fields=["status", "reviewed_by", "reviewed_at", "review_notes", "updated_at"])
        messages.success(request, "تم رفض الطلب وإظهار السبب لمقدم الطلب.")
        return redirect("reports:platform_school_addition_requests")

    if action != "approve":
        messages.error(request, "الإجراء المطلوب غير صحيح.")
        return redirect("reports:platform_school_addition_requests")

    try:
        with transaction.atomic():
            _provision_school_for_request(
                addition_request.pk,
                reviewed_by=request.user,
                review_notes=review_notes,
            )
    except ValidationError as exc:
        error_messages = getattr(exc, "messages", None)
        messages.error(
            request,
            " ".join(error_messages) if error_messages else str(exc),
        )
        return redirect("reports:platform_school_addition_requests")

    messages.success(request, "تم اعتماد الطلب وإنشاء المدرسة وربطها بحساب المدير.")
    return redirect("reports:platform_school_addition_requests")
