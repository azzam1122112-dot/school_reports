# -*- coding: utf-8 -*-
"""شاشات الاجتماعات والقرارات.

شاشتان: قائمة الاجتماعات، وصفحة الاجتماع الواحد التي تجمع جدول الأعمال والحضور
والمحضر والقرارات في مكان واحد. تفريقها على أربع صفحات كان سيجعل كتابة محضر
واحد رحلةً بين شاشات، وهي عملٌ يُنجَز في جلسة واحدة.

**اعتماد المحضر لا يُكتب هنا.** ``MeetingMinutes`` يرث ``ApprovalMixin``،
فيمرّ بـ ``ACTION_DISPATCH`` نفسه الذي يخدم التقارير والتكليفات — وقاعدة «لا
يعتمد أحد عمله» تسري على كاتب المحضر بلا سطر جديد.
"""
from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Max, Q
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from .. import capabilities as caps
from ..forms_meetings import (
    AgendaItemForm,
    DecisionForm,
    MinutesForm,
    SchoolMeetingForm,
)
from ..models import Meeting, MeetingAgendaItem, MeetingAttendee
from ..permissions import capability_source, is_school_manager
from ..services_approval import (
    ACTION_DISPATCH,
    ApprovalError,
    available_actions,
    transitions_for,
)
from ..services_meetings import (
    MeetingError,
    cancel_meeting,
    convert_decision_to_assignment,
    decision_followup_rows,
    ensure_minutes,
    mark_held,
    meetings_for_user,
    set_attendance,
)
from ._helpers import *  # noqa: F401,F403
from ._helpers import _get_active_school

__all__ = [
    "meeting_list",
    "meeting_create",
    "meeting_detail",
    "meeting_action",
    "minutes_approval_action",
]


def _school_or_redirect(request):
    school = _get_active_school(request)
    if school is None:
        messages.error(request, "فضلاً اختر مدرسة أولاً.")
        return None, redirect("reports:select_school")
    return school, None


def _may_organize(user, school) -> bool:
    if is_school_manager(user, active_school=school):
        return True
    return capability_source(user, caps.MANAGE_MEETINGS, school) is not None


def _meeting_for(request, pk: int, school) -> Meeting:
    """الاجتماع الذي يحق لهذا المستخدم رؤيته.

    منظّمه، أو مدعوّ إليه، أو مدير المدرسة. وما عدا ذلك يُعامَل كغير موجود — لا
    كممنوع، لئلا يُكشف انعقاد اجتماع لمن لا يحق له معرفة أنه انعقد.
    """
    meeting = get_object_or_404(
        Meeting.objects.select_related("organizer", "department", "school", "group"),
        pk=pk,
    )
    if meeting.organizer_id == request.user.pk:
        return meeting
    if meeting.attendees.filter(person=request.user).exists():
        return meeting
    if meeting.school_id == getattr(school, "pk", None) and is_school_manager(
        request.user, active_school=school
    ):
        return meeting
    raise Http404


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def meeting_list(request):
    """اجتماعاتي: ما نظّمته وما دُعيت إليه."""
    school, redirect_response = _school_or_redirect(request)
    if redirect_response is not None:
        return redirect_response

    meetings = list(meetings_for_user(request.user, school=school)[:100])
    upcoming = [m for m in meetings if m.status == Meeting.Status.SCHEDULED]
    held = [m for m in meetings if m.status == Meeting.Status.HELD]

    # محاضر تنتظر كتابةً أو اعتماداً — أول ما يهمّ من يفتح الشاشة.
    pending_minutes = [
        m
        for m in held
        if getattr(m, "minutes", None) is None
        or not getattr(m.minutes, "is_final", False)
    ]

    return render(
        request,
        "reports/meeting_list.html",
        {
            "active": "meeting_list",
            "active_school": school,
            "upcoming": upcoming,
            "held": held,
            "pending_minutes_count": len(pending_minutes),
            "can_organize": _may_organize(request.user, school),
        },
    )


@login_required(login_url="reports:login")
@require_http_methods(["GET", "POST"])
def meeting_create(request):
    """تنظيم اجتماع جديد داخل المدرسة."""
    school, redirect_response = _school_or_redirect(request)
    if redirect_response is not None:
        return redirect_response

    if not _may_organize(request.user, school):
        messages.error(request, "لا تملك صلاحية تنظيم الاجتماعات.")
        return redirect("reports:home")

    form = SchoolMeetingForm(request.POST or None, school=school, organizer=request.user)

    if request.method == "POST" and form.is_valid():
        with transaction.atomic():
            meeting = form.save(commit=False)
            meeting.organizer = request.user
            meeting.save()
            for person in form.cleaned_data["attendees"]:
                MeetingAttendee.objects.create(meeting=meeting, person=person)
        messages.success(request, "أُنشئ الاجتماع ووُجّهت الدعوات.")
        return redirect("reports:meeting_detail", pk=meeting.pk)

    if request.method == "POST":
        messages.error(request, "تعذّر إنشاء الاجتماع — تحقّق من الحقول.")

    return render(
        request,
        "reports/meeting_create.html",
        {"active": "meeting_list", "active_school": school, "form": form},
    )


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def meeting_detail(request, pk: int):
    """صفحة الاجتماع: جدول الأعمال والحضور والمحضر والقرارات."""
    school, redirect_response = _school_or_redirect(request)
    if redirect_response is not None:
        return redirect_response

    meeting = _meeting_for(request, pk, school)
    is_organizer = meeting.organizer_id == request.user.pk

    minutes = getattr(meeting, "minutes", None)
    if minutes is None and is_organizer and meeting.is_held:
        minutes = ensure_minutes(meeting, recorder=request.user)

    minutes_actions = (
        available_actions(minutes, request.user, school=meeting.school)
        if minutes is not None
        else []
    )

    return render(
        request,
        "reports/meeting_detail.html",
        {
            "active": "meeting_list",
            "active_school": school,
            "meeting": meeting,
            "is_organizer": is_organizer,
            "agenda": list(meeting.agenda_items.all()),
            "attendees": list(meeting.attendees.select_related("person")),
            "attendance": meeting.attendance_summary,
            "attendance_choices": MeetingAttendee.Status.choices,
            "minutes": minutes,
            "minutes_form": MinutesForm(instance=minutes) if minutes is not None else None,
            "minutes_actions": minutes_actions,
            "minutes_timeline": list(transitions_for(minutes)) if minutes is not None else [],
            "agenda_form": AgendaItemForm(),
            "decision_form": DecisionForm(meeting=meeting),
            "decisions": decision_followup_rows(meeting),
        },
    )


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def meeting_action(request, pk: int):
    """إجراءات المنظّم على اجتماعه."""
    school, redirect_response = _school_or_redirect(request)
    if redirect_response is not None:
        return redirect_response

    meeting = _meeting_for(request, pk, school)
    action = (request.POST.get("meeting_action") or "").strip()

    try:
        if action == "add_agenda":
            form = AgendaItemForm(request.POST)
            if meeting.organizer_id != request.user.pk:
                raise PermissionDenied("جدول الأعمال يعدّه منظّم الاجتماع.")
            if not form.is_valid():
                messages.error(request, "اكتب عنوان البند.")
            else:
                item = form.save(commit=False)
                item.meeting = meeting
                item.order = (
                    meeting.agenda_items.aggregate(top=Max("order"))["top"] or 0
                ) + 1
                item.save()
                messages.success(request, "أُضيف بند إلى جدول الأعمال.")

        elif action == "remove_agenda":
            if meeting.organizer_id != request.user.pk:
                raise PermissionDenied("جدول الأعمال يعدّه منظّم الاجتماع.")
            item = get_object_or_404(
                MeetingAgendaItem, pk=request.POST.get("item_id"), meeting=meeting
            )
            item.delete()
            messages.success(request, "حُذف البند.")

        elif action == "mark_held":
            mark_held(meeting, request.user)
            ensure_minutes(meeting, recorder=request.user)
            messages.success(request, "سُجِّل انعقاد الاجتماع، وفُتح المحضر للكتابة.")

        elif action == "cancel":
            cancel_meeting(meeting, request.user, reason=request.POST.get("reason", ""))
            messages.success(request, "أُلغي الاجتماع. تبقى دعوته في السجل.")

        elif action == "attendance":
            rows = {
                key.split("attendance_", 1)[1]: value
                for key, value in request.POST.items()
                if key.startswith("attendance_")
            }
            set_attendance(meeting, request.user, rows=rows)
            messages.success(request, "سُجِّل الحضور.")

        elif action == "save_minutes":
            minutes = ensure_minutes(meeting, recorder=request.user)
            if minutes.recorder_id not in (None, request.user.pk):
                raise PermissionDenied("المحضر يكتبه من فُتح باسمه.")
            if not minutes.is_editable_by_owner:
                raise MeetingError("المحضر ليس في حالة تسمح بتعديله.")
            form = MinutesForm(request.POST, instance=minutes)
            if not form.is_valid():
                messages.error(request, "تعذّر حفظ المحضر.")
            else:
                obj = form.save(commit=False)
                if obj.recorder_id is None:
                    obj.recorder = request.user
                obj.save()
                messages.success(request, "حُفظ المحضر.")

        elif action == "add_decision":
            if meeting.organizer_id != request.user.pk:
                raise PermissionDenied("تسجيل القرارات لمنظّم الاجتماع.")
            form = DecisionForm(request.POST, meeting=meeting)
            if not form.is_valid():
                messages.error(request, "تعذّر تسجيل القرار — تحقّق من الحقول.")
            else:
                decision = form.save(commit=False)
                decision.meeting = meeting
                decision.order = (
                    meeting.decisions.aggregate(top=Max("order"))["top"] or 0
                ) + 1
                decision.save()
                messages.success(request, "سُجِّل القرار.")

        elif action == "track_decision":
            decision = get_object_or_404(
                meeting.decisions.all(), pk=request.POST.get("decision_id")
            )
            convert_decision_to_assignment(decision, request.user)
            messages.success(
                request, "حُوِّل القرار إلى تكليف — يُتابَع الآن بموعده وشواهده."
            )

        else:
            messages.error(request, "إجراء غير معروف.")

    except PermissionDenied as exc:
        messages.error(request, str(exc) or "لا تملك هذا الإجراء.")
    except (MeetingError, ApprovalError, ValidationError) as exc:
        detail = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, detail[0])

    return redirect("reports:meeting_detail", pk=pk)


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def minutes_approval_action(request, pk: int):
    """دورة اعتماد المحضر — بالمكوّن المشترك نفسه."""
    school, redirect_response = _school_or_redirect(request)
    if redirect_response is not None:
        return redirect_response

    meeting = _meeting_for(request, pk, school)
    minutes = getattr(meeting, "minutes", None)
    if minutes is None:
        messages.error(request, "لم يُفتح محضر لهذا الاجتماع بعد.")
        return redirect("reports:meeting_detail", pk=pk)

    action = (request.POST.get("approval_action") or "").strip()
    note = (request.POST.get("note") or "").strip()

    handler = ACTION_DISPATCH.get(action)
    if handler is None or action not in available_actions(
        minutes, request.user, school=meeting.school
    ):
        messages.error(request, "هذا الإجراء غير متاح على المحضر الآن.")
        return redirect("reports:meeting_detail", pk=pk)

    try:
        handler(minutes, request.user, school=meeting.school, note=note)
    except PermissionDenied as exc:
        messages.error(request, str(exc) or "لا تملك هذا الإجراء.")
    except (ApprovalError, ValidationError) as exc:
        detail = getattr(exc, "messages", None) or [str(exc)]
        messages.error(request, detail[0])
    else:
        messages.success(
            request,
            {
                "submit": "أُرسل المحضر للاعتماد.",
                "withdraw": "سُحب المحضر للتعديل.",
                "start_review": "بدأت مراجعة المحضر.",
                "request_info": "طُلب استكمال من كاتب المحضر.",
                "return": "أُعيد المحضر لكاتبه مع ملاحظتك.",
                "approve": "اعتُمد المحضر.",
            }.get(action, "نُفِّذ الإجراء."),
        )

    return redirect("reports:meeting_detail", pk=pk)
