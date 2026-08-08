# -*- coding: utf-8 -*-
"""تعاميم المدير التنفيذي على مدارس مجموعته، وتقرير اطّلاعها.

حدّ الصلاحية الذي ينفّذه هذا الملف: المدير التنفيذي يملك **تواصلاً على مستوى
المجموعة** — يُنشئ تعاميم موجَّهة لمدارسه ويتابع اطّلاعها — ولا يملك أي سلطة
على البيانات التشغيلية لأي مدرسة. فهو لا يعدّل ولا يحذف تعاميم المدرسة، ولا
يرى تعميماً لم يرسله هو.

التسليم بالتفريع: كل مدرسة مستهدفة تستقبل ``Notification`` مستقلاً بمدرستها
الصحيحة، فيراه مديرها تعميماً طبيعياً في شاشته المعتادة بلا أي تغيير في
المنطق القائم. والدفعة أبٌ يقرأ منه المدير التنفيذي تقريراً موحّداً.
"""
from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_http_methods

from ..forms_group_notifications import GroupNotificationForm
from ..models import (
    GroupNotificationBatch,
    Notification,
    NotificationRecipient,
    SchoolMembership,
    Teacher,
)
from ..permissions import (
    can_send_group_notification,
    can_view_group_batch,
    executive_director_groups,
    executive_director_schools_qs,
    is_executive_director,
)
from ..services_archive import archive_storage_capacity_error

logger = logging.getLogger(__name__)

__all__ = ["group_notification_create", "group_notification_report", "group_notifications_sent"]

DEFAULT_ACK_TEXT = "أقرّ بأنني اطلعت على هذا التعميم وفهمت ما ورد فيه وأتعهد بالالتزام به."


def _require_director(request):
    if not is_executive_director(request.user):
        raise Http404


def _recipient_ids_for(school, audience: str) -> list[int]:
    """معرّفات مستلمي مدرسة واحدة حسب الفئة المختارة."""
    roles = [SchoolMembership.RoleType.MANAGER]
    if audience == GroupNotificationBatch.Audience.ALL:
        # «جميع المنسوبين» = ``STAFF_ROLES`` لا المعلّمون وحدهم. إضافة
        # ``TEACHER`` وحده تُسقط الوكيل والموظف الإداري ومحضّر المختبر من
        # مستقبلي التعميم بلا خطأ واحد — وهو الفشل الصامت الذي سُمّيت
        # ``STAFF_ROLES`` لمنعه.
        roles.extend(SchoolMembership.STAFF_ROLES)

    return list(
        Teacher.objects.filter(
            is_active=True,
            school_memberships__school=school,
            school_memberships__is_active=True,
            school_memberships__role_type__in=roles,
        )
        .values_list("id", flat=True)
        .distinct()
    )


@login_required
@require_http_methods(["GET", "POST"])
def group_notification_create(request):
    _require_director(request)

    allowed = executive_director_schools_qs(request.user)
    form = GroupNotificationForm(
        request.POST or None,
        request.FILES or None,
        allowed_schools=allowed,
    )

    if request.method == "POST" and form.is_valid():
        schools = list(form.cleaned_data["schools"])
        school_ids = [school.pk for school in schools]

        # خط الدفاع الثاني بعد تقييد queryset في النموذج.
        if not can_send_group_notification(request.user, school_ids):
            raise Http404

        audience = form.cleaned_data["audience"]
        attachment = form.cleaned_data.get("attachment")

        # المرفق يُحتسب على تخزين كل مدرسة، فيُفحص لكلٍّ منها قبل إنشاء أي صف.
        # الفشل الجزئي هنا أسوأ من الرفض: تعميم وصل نصف المدارس يظنّه المرسِل واصلاً.
        if attachment:
            for school in schools:
                capacity_error = archive_storage_capacity_error(school, [attachment])
                if capacity_error:
                    form.add_error("attachment", f"{school.name}: {capacity_error}")
                    break

        if not form.errors:
            recipients_by_school = {
                school.pk: _recipient_ids_for(school, audience) for school in schools
            }
            empty = [school.name for school in schools if not recipients_by_school[school.pk]]
            if empty:
                form.add_error(
                    "schools",
                    "لا يوجد مستلمون مطابقون في: " + "، ".join(empty),
                )

        if not form.errors:
            group = schools[0].group
            try:
                with transaction.atomic():
                    batch = GroupNotificationBatch.objects.create(
                        group=group,
                        sender=request.user,
                        audience=audience,
                        title=form.cleaned_data.get("title") or "",
                        requires_signature=bool(form.cleaned_data.get("requires_signature")),
                    )
                    _fan_out(batch, schools, recipients_by_school, form.cleaned_data)
            except Exception:
                logger.exception("group notification send failed")
                messages.error(request, "تعذّر الإرسال. جرّب لاحقاً.")
            else:
                total = sum(len(ids) for ids in recipients_by_school.values())
                messages.success(
                    request,
                    f"✅ أُرسل التعميم إلى {len(schools)} مدرسة و{total} مستلماً.",
                )
                return redirect("reports:group_notification_report", pk=batch.pk)

    return render(
        request,
        "reports/group_notification_create.html",
        {
            "form": form,
            "groups": list(executive_director_groups(request.user)),
            "schools": list(allowed),
            "default_ack_text": DEFAULT_ACK_TEXT,
            "active": "group_notifications",
        },
    )


def _fan_out(batch, schools, recipients_by_school, cleaned) -> None:
    """إشعار مستقل لكل مدرسة، ومستلموه يُنشأون فوراً.

    إنشاء صفوف المستلمين هنا لا في Celery مقصود، وهو نفس ضمان الموثوقية المتّبع
    في إرسال المدرسة: التسليم داخل المنصة لا يتوقف على عاملٍ حيّ.
    """
    from ..cache_utils import invalidate_user_notifications

    requires_signature = bool(cleaned.get("requires_signature"))
    ack_text = (cleaned.get("signature_ack_text") or "").strip() or DEFAULT_ACK_TEXT

    for school in schools:
        notification = Notification.objects.create(
            title=cleaned.get("title") or "",
            message=cleaned["message"],
            is_important=bool(cleaned.get("is_important")),
            attachment=cleaned.get("attachment") if requires_signature else None,
            requires_signature=requires_signature,
            signature_deadline_at=cleaned.get("signature_deadline_at") if requires_signature else None,
            signature_ack_text=ack_text if requires_signature else "",
            created_by=batch.sender,
            school=school,
            batch=batch,
        )
        teacher_ids = recipients_by_school[school.pk]
        NotificationRecipient.objects.bulk_create(
            [NotificationRecipient(notification=notification, teacher_id=tid) for tid in teacher_ids],
            ignore_conflicts=True,
        )
        for tid in teacher_ids:
            try:
                invalidate_user_notifications(int(tid))
            except Exception:
                logger.exception("notification cache invalidation failed for teacher %s", tid)


@login_required
@require_http_methods(["GET"])
def group_notifications_sent(request):
    _require_director(request)

    batches = (
        GroupNotificationBatch.objects.filter(
            sender=request.user,
            group__in=executive_director_groups(request.user),
        )
        .select_related("group")
        .prefetch_related("notifications__school")
    )
    rows = []
    for batch in batches:
        notifications = list(batch.notifications.all())
        stats = _batch_stats([n.pk for n in notifications])
        rows.append(
            {
                "batch": batch,
                "schools": len(notifications),
                "total": stats["total"],
                "read": stats["read"],
                "signed": stats["signed"],
                "read_percent": stats["read_percent"],
            }
        )

    return render(
        request,
        "reports/group_notifications_sent.html",
        {"rows": rows, "active": "group_notifications"},
    )


def _batch_stats(notification_ids) -> dict:
    if not notification_ids:
        return {"total": 0, "read": 0, "unread": 0, "signed": 0, "read_percent": 0}
    qs = NotificationRecipient.objects.filter(notification_id__in=notification_ids)
    total = qs.count()
    read = qs.filter(is_read=True).count()
    signed = qs.filter(is_signed=True).count()
    return {
        "total": total,
        "read": read,
        "unread": total - read,
        "signed": signed,
        "read_percent": round(read * 100 / total) if total else 0,
    }


@login_required
@require_http_methods(["GET"])
def group_notification_report(request, pk: int):
    """تقرير الاطّلاع، مجمَّعاً على مستوى المجموعة ومفصَّلاً لكل مدرسة."""
    _require_director(request)

    batch = get_object_or_404(
        GroupNotificationBatch.objects.select_related("group"), pk=pk
    )
    if not can_view_group_batch(request.user, batch):
        raise Http404

    notifications = list(
        batch.notifications.select_related("school").order_by("school__name")
    )
    recipients = (
        NotificationRecipient.objects.filter(notification__in=notifications)
        .select_related("teacher", "notification__school")
        .order_by("teacher__name")
    )

    # مديرو كل مدارس الدفعة في استعلام واحد: التمييز بين «مدير» و«معلم» في
    # التقرير لا يصح أن يكلّف استعلاماً لكل مستلم.
    manager_pairs = set(
        SchoolMembership.objects.filter(
            school__in=[n.school_id for n in notifications if n.school_id],
            is_active=True,
            role_type=SchoolMembership.RoleType.MANAGER,
        ).values_list("school_id", "teacher_id")
    )

    by_school: dict[int, dict] = {}
    for notification in notifications:
        by_school[notification.pk] = {
            "school": notification.school,
            "notification": notification,
            "people": [],
            "read": 0,
            "signed": 0,
        }

    for recipient in recipients:
        bucket = by_school.get(recipient.notification_id)
        if bucket is None:
            continue
        is_read = bool(recipient.is_read)
        is_signed = bool(recipient.is_signed)
        bucket["read"] += 1 if is_read else 0
        bucket["signed"] += 1 if is_signed else 0
        bucket["people"].append(
            {
                "name": recipient.teacher.name or recipient.teacher.phone,
                "is_manager": (
                    recipient.notification.school_id,
                    recipient.teacher_id,
                ) in manager_pairs,
                "is_read": is_read,
                "read_at": recipient.read_at,
                "is_signed": is_signed,
                "signed_at": recipient.signed_at,
            }
        )

    rows = []
    for bucket in by_school.values():
        total = len(bucket["people"])
        bucket["total"] = total
        bucket["read_percent"] = round(bucket["read"] * 100 / total) if total else 0
        bucket["pending"] = [person for person in bucket["people"] if not person["is_read"]]
        rows.append(bucket)

    # الأقل اطّلاعاً أولاً: هو ما يحتاج تدخّل المدير التنفيذي.
    rows.sort(key=lambda row: (row["read_percent"], row["school"].name if row["school"] else ""))

    totals = _batch_stats([n.pk for n in notifications])

    return render(
        request,
        "reports/group_notification_report.html",
        {
            "batch": batch,
            "rows": rows,
            "totals": totals,
            "active": "group_notifications",
        },
    )
