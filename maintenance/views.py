from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpRequest, HttpResponse, HttpResponseForbidden, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views.decorators.http import require_http_methods

from reports.models import AuditLog, School

from .models import SchoolYearResetJob
from .services import (
    CONFIRM_PHRASE,
    INCLUDE_KEYS,
    build_file_manifest,
    collect_file_keys,
    collect_reset_summary,
    execute_school_year_reset,
    normalize_include_options,
    resolve_target_schools,
)

logger = logging.getLogger(__name__)


def _is_system_admin(user) -> bool:
    return bool(getattr(user, "is_authenticated", False) and getattr(user, "is_superuser", False))


def _parse_multiline_values(raw: str) -> list[str]:
    values: list[str] = []
    for part in (raw or "").replace(",", "\n").splitlines():
        value = part.strip()
        if value:
            values.append(value)
    return values


def _write_preview_audit(job: SchoolYearResetJob) -> None:
    try:
        AuditLog.objects.create(
            teacher=job.created_by,
            action=AuditLog.Action.CREATE,
            model_name="SchoolYearResetJob",
            object_id=job.pk,
            object_repr=f"SchoolYearResetJob #{job.pk}",
            changes={
                "status": job.status,
                "school_ids": job.dry_run_summary.get("school_ids", []),
                "include_options": job.dry_run_summary.get("include_options", {}),
                "delete_files": job.delete_files,
            },
        )
    except Exception:
        logger.exception("school year reset: failed to write preview audit log job_id=%s", job.pk)


def _include_options_from_post(request: HttpRequest) -> dict[str, bool]:
    return normalize_include_options({key: key in request.POST for key in INCLUDE_KEYS})


def _job_summary_cards(summary: dict) -> list[dict[str, object]]:
    return [
        {"label": "المدارس المستهدفة", "value": summary.get("schools_count", 0)},
        {"label": "التقارير", "value": summary.get("reports_count", 0)},
        {"label": "الطلبات والتذاكر", "value": summary.get("tickets_count", 0)},
        {"label": "صور التذاكر", "value": summary.get("ticket_images_count", 0)},
        {"label": "ملفات الإنجاز", "value": summary.get("achievements_count", 0)},
        {"label": "صور شواهد الإنجاز", "value": summary.get("achievement_evidence_images_count", 0)},
        {"label": "تقارير الإنجاز المؤرشفة", "value": summary.get("achievement_evidence_reports_count", 0)},
        {"label": "الإشعارات والتعاميم", "value": summary.get("notifications_count", 0)},
        {"label": "مستلمي الإشعارات", "value": summary.get("notification_recipients_count", 0)},
        {"label": "روابط المشاركة", "value": summary.get("share_links_count", 0)},
        {"label": "ملفات التخزين المرشحة", "value": summary.get("file_keys_count", 0)},
        {"label": "مدارس محمية بالأرشيف", "value": summary.get("archive_protected_schools_count", 0)},
    ]


def _recent_job_rows() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    jobs = list(
        SchoolYearResetJob.objects.select_related("created_by")
        .prefetch_related("schools")
        .order_by("-created_at", "-id")[:10]
    )
    for job in jobs:
        rows.append(
            {
                "id": job.id,
                "status_label": job.get_status_display(),
                "created_at": job.created_at,
                "schools_count": job.schools.count(),
            }
        )
    return rows


def _render_page(request: HttpRequest, *, preview_job: SchoolYearResetJob | None = None) -> HttpResponse:
    summary = preview_job.dry_run_summary if preview_job else {}
    selected_schools = []
    if preview_job:
        selected_schools = list(preview_job.schools.order_by("name", "id")[:20])

    context = {
        "confirm_phrase": CONFIRM_PHRASE,
        "include_keys": INCLUDE_KEYS,
        "recent_jobs": _recent_job_rows(),
        "preview_job": preview_job,
        "preview_status_label": preview_job.get_status_display() if preview_job else "",
        "summary_cards": _job_summary_cards(summary) if summary else [],
        "file_samples": (summary.get("file_key_samples") or [])[:20] if summary else [],
        "selected_schools": selected_schools,
        "schools_total": School.objects.count(),
        "school_search_url": reverse("maintenance:school_year_reset_school_search"),
        "default_include": {key: True for key in INCLUDE_KEYS},
    }
    return render(request, "maintenance/school_year_reset.html", context)


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def school_year_reset_school_search(request: HttpRequest) -> JsonResponse:
    if not _is_system_admin(request.user):
        return JsonResponse({"error": "forbidden"}, status=403)

    query = (request.GET.get("q") or "").strip()
    page_number = request.GET.get("page") or 1
    qs = School.objects.all().order_by("name", "id")
    if query:
        qs = qs.filter(
            Q(name__icontains=query)
            | Q(code__icontains=query)
            | Q(city__icontains=query)
            | Q(phone__icontains=query)
        )

    paginator = Paginator(qs.only("id", "name", "code", "city", "stage", "gender", "is_active"), 20)
    page = paginator.get_page(page_number)
    results = [
        {
            "id": school.id,
            "name": school.name,
            "code": school.code,
            "city": school.city or "",
            "stage": school.get_stage_display(),
            "gender": school.get_gender_display(),
            "is_active": bool(school.is_active),
        }
        for school in page.object_list
    ]
    return JsonResponse(
        {
            "results": results,
            "page": page.number,
            "has_next": page.has_next(),
            "total": paginator.count,
        }
    )


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def school_year_reset(request: HttpRequest) -> HttpResponse:
    if not _is_system_admin(request.user):
        return HttpResponseForbidden("هذه الصفحة متاحة لمدير النظام فقط.")

    if request.method == "GET":
        return _render_page(request)

    if not _is_system_admin(request.user):
        return HttpResponseForbidden("هذه العملية متاحة لمدير النظام فقط.")

    action = request.POST.get("action", "")
    if action == "preview":
        try:
            target_mode = request.POST.get("target_mode", "specific")
            schools = list(
                resolve_target_schools(
                    all_schools=target_mode == "all",
                    school_ids=_parse_multiline_values(request.POST.get("school_ids", "")),
                    school_codes=_parse_multiline_values(request.POST.get("school_codes", "")),
                )
            )
            if not schools:
                messages.error(request, "لم يتم العثور على مدارس مطابقة.")
                return _render_page(request)

            include_options = _include_options_from_post(request)
            delete_files = request.POST.get("delete_files") == "on"
            summary = collect_reset_summary(schools, include_options)
            manifest = build_file_manifest(collect_file_keys(schools, include_options))

            job = SchoolYearResetJob.objects.create(
                created_by=request.user,
                status=SchoolYearResetJob.Status.PREVIEWED,
                include_reports=include_options["reports"],
                include_tickets=include_options["tickets"],
                include_achievements=include_options["achievements"],
                include_notifications=include_options["notifications"],
                include_share_links=include_options["share_links"],
                delete_files=delete_files,
                dry_run_summary=summary,
                file_manifest=manifest,
            )
            job.schools.set(schools)
            _write_preview_audit(job)
            logger.warning("school year reset preview created job_id=%s by=%s", job.id, request.user.pk)
            messages.success(request, "تم إنشاء معاينة آمنة. لم يتم حذف أي بيانات.")
            return _render_page(request, preview_job=job)
        except ValueError as exc:
            messages.error(request, str(exc))
            return _render_page(request)

    if action == "execute":
        job = get_object_or_404(SchoolYearResetJob, pk=request.POST.get("job_id"))
        if job.status != SchoolYearResetJob.Status.PREVIEWED:
            messages.error(request, "لا يمكن تنفيذ العملية إلا بعد معاينة حديثة.")
            return redirect(reverse("maintenance:school_year_reset"))
        if request.POST.get("confirm", "").strip() != CONFIRM_PHRASE:
            messages.error(request, f"للتنفيذ النهائي اكتب {CONFIRM_PHRASE} كما هي.")
            return _render_page(request, preview_job=job)
        try:
            execute_school_year_reset(job)
            job.refresh_from_db()
            if job.status == SchoolYearResetJob.Status.PARTIAL:
                messages.warning(request, "تم حذف البيانات، لكن تعذر حذف بعض الملفات من التخزين. راجع سجل العملية.")
            else:
                messages.success(request, "تمت تهيئة العام الدراسي بنجاح.")
            return redirect(reverse("maintenance:school_year_reset"))
        except Exception as exc:
            messages.error(request, f"فشل التنفيذ: {exc}")
            logger.exception("school year reset execution failed from UI job_id=%s", job.pk)
            return _render_page(request, preview_job=job)

    messages.error(request, "إجراء غير معروف.")
    return _render_page(request)
