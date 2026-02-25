from __future__ import annotations

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from reports.models import (
    AuditLog,
    Notification,
    NotificationRecipient,
    Report,
    School,
    SchoolMembership,
    SchoolSubscription,
    Ticket,
)
from reports.cache_utils import invalidate_school, invalidate_user_notifications

User = get_user_model()

try:
    from .realtime_notifications import (
        push_delta_to_user,
        push_force_resync,
        push_new_notification_to_teachers,
    )
except Exception:  # pragma: no cover
    push_delta_to_user = None  # type: ignore
    push_force_resync = None  # type: ignore
    push_new_notification_to_teachers = None  # type: ignore


def _infer_school_for_audit(request, user) -> School | None:
    """Best-effort school inference for audit events.

    - Prefer the active school in session.
    - Otherwise, if the user has exactly one active membership, use it.
    - Otherwise, return None.
    """
    if request is None or user is None:
        return None

    try:
        sid = request.session.get("active_school_id")
    except Exception:
        sid = None

    try:
        if sid:
            school = School.objects.filter(pk=sid, is_active=True).first()
            if school is not None:
                return school
    except Exception:
        pass

    try:
        schools = (
            School.objects.filter(memberships__teacher=user, memberships__is_active=True, is_active=True)
            .distinct()
            .order_by("id")
        )
        if schools.count() == 1:
            return schools.first()
    except Exception:
        pass

    return None


@receiver(user_logged_in)
def _single_session_on_login(sender, request, user, **kwargs):
    """Ensure a user can only have one active session.

    On login:
    - Ensure the current session has a key.
    - Delete the previously recorded session (if any).
    - Persist the new session key on the user.

    Notes:
    - This relies on the DB-backed session engine (default). If a different
      session backend is used, deleting the old session may be a no-op.
    """
    try:
        if request.session.session_key is None:
            request.session.save()
        new_key = request.session.session_key or ""
    except Exception:
        return

    try:
        old_key = getattr(user, "current_session_key", "") or ""
    except Exception:
        old_key = ""

    if old_key and new_key and old_key != new_key:
        try:
            from django.contrib.sessions.models import Session

            Session.objects.filter(session_key=old_key).delete()
        except Exception:
            # If sessions aren't DB-backed, we can't force-delete the old one.
            pass

    try:
        if getattr(user, "current_session_key", "") != new_key:
            user.current_session_key = new_key
            user.save(update_fields=["current_session_key"])
    except Exception:
        pass

    # Audit: login
    try:
        AuditLog.objects.create(
            school=_infer_school_for_audit(request, user),
            teacher=user,
            action=AuditLog.Action.LOGIN,
            model_name="Auth",
            object_id=getattr(user, "pk", None),
            object_repr=f"Login: {str(user)[:200]}",
            ip_address=(request.META.get("REMOTE_ADDR") if request else None),
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:500] if request else ""),
        )
    except Exception:
        # لا نكسر تسجيل الدخول بسبب فشل سجل العمليات
        pass


@receiver(user_logged_out)
def _single_session_on_logout(sender, request, user, **kwargs):
    """Clear recorded session key when the active session logs out."""
    if not user:
        return

    try:
        sk = request.session.session_key or ""
    except Exception:
        sk = ""

    try:
        if sk and getattr(user, "current_session_key", "") == sk:
            user.current_session_key = ""
            user.save(update_fields=["current_session_key"])
    except Exception:
        pass


# =========================
# Realtime Notifications (Channels / WebSocket)
# =========================


@receiver(pre_save, sender=NotificationRecipient)
def _notif_recipient_pre_save(sender, instance: NotificationRecipient, **kwargs):
    """Capture previous values for delta calculation.

    Important: queryset.update()/bulk_update won't trigger this.
    """
    try:
        if not getattr(instance, "pk", None):
            return
    except Exception:
        return

    try:
        old = (
            NotificationRecipient.objects.select_related("notification")
            .only(
                "id",
                "is_read",
                "is_signed",
                "notification__requires_signature",
                "notification__school_id",
            )
            .filter(pk=instance.pk)
            .first()
        )
        if old is None:
            return
        instance._sr_old_is_read = bool(getattr(old, "is_read", False))
        instance._sr_old_is_signed = bool(getattr(old, "is_signed", False))
        n = getattr(old, "notification", None)
        instance._sr_old_requires_signature = bool(getattr(n, "requires_signature", False)) if n else False
        instance._sr_old_notification_school_id = getattr(n, "school_id", None) if n else None
    except Exception:
        return


@receiver(post_save, sender=NotificationRecipient)
def _notif_recipient_post_save(sender, instance: NotificationRecipient, created: bool, **kwargs):
    """Push counter updates to the recipient over WebSocket."""
    if push_delta_to_user is None:
        return

    try:
        teacher_id = int(getattr(instance, "teacher_id", 0) or 0)
        if teacher_id <= 0:
            return
    except Exception:
        return

    try:
        n = getattr(instance, "notification", None)
        requires_signature = bool(getattr(n, "requires_signature", False)) if n else False
        notif_school_id = getattr(n, "school_id", None) if n else None
    except Exception:
        requires_signature = False
        notif_school_id = None

    if created:
        # New recipient row == new attention item.
        try:
            if requires_signature:
                push_delta_to_user(
                    teacher_id=teacher_id,
                    notification_school_id=notif_school_id,
                    delta_signatures_pending=1,
                    delta_count=1,
                )
            else:
                push_delta_to_user(
                    teacher_id=teacher_id,
                    notification_school_id=notif_school_id,
                    delta_unread=1,
                    delta_count=1,
                )
        except Exception:
            pass
        return

    # Updates: read/signature state change
    try:
        old_is_read = getattr(instance, "_sr_old_is_read", None)
        old_is_signed = getattr(instance, "_sr_old_is_signed", None)
        old_requires_signature = getattr(instance, "_sr_old_requires_signature", requires_signature)
        old_school_id = getattr(instance, "_sr_old_notification_school_id", notif_school_id)
    except Exception:
        old_is_read = None
        old_is_signed = None
        old_requires_signature = requires_signature
        old_school_id = notif_school_id

    # If schema/instance didn't have old values, do a safe resync.
    if old_is_read is None and old_is_signed is None:
        if push_force_resync is not None:
            try:
                push_force_resync(teacher_id=teacher_id)
            except Exception:
                pass
        return

    # Circulars: count depends on is_signed only.
    if bool(old_requires_signature):
        try:
            new_is_signed = bool(getattr(instance, "is_signed", False))
            if old_is_signed is False and new_is_signed is True:
                push_delta_to_user(
                    teacher_id=teacher_id,
                    notification_school_id=old_school_id,
                    delta_signatures_pending=-1,
                    delta_count=-1,
                )
            elif old_is_signed is True and new_is_signed is False:
                push_delta_to_user(
                    teacher_id=teacher_id,
                    notification_school_id=old_school_id,
                    delta_signatures_pending=1,
                    delta_count=1,
                )
        except Exception:
            pass
        return

    # Normal notifications: count depends on is_read.
    try:
        new_is_read = bool(getattr(instance, "is_read", False))
        if old_is_read is False and new_is_read is True:
            push_delta_to_user(
                teacher_id=teacher_id,
                notification_school_id=old_school_id,
                delta_unread=-1,
                delta_count=-1,
            )
        elif old_is_read is True and new_is_read is False:
            push_delta_to_user(
                teacher_id=teacher_id,
                notification_school_id=old_school_id,
                delta_unread=1,
                delta_count=1,
            )
    except Exception:
        pass

    # Audit: logout
    try:
        AuditLog.objects.create(
            school=_infer_school_for_audit(request, user),
            teacher=user,
            action=AuditLog.Action.LOGOUT,
            model_name="Auth",
            object_id=getattr(user, "pk", None),
            object_repr=f"Logout: {str(user)[:200]}",
            ip_address=(request.META.get("REMOTE_ADDR") if request else None),
            user_agent=(request.META.get("HTTP_USER_AGENT", "")[:500] if request else ""),
        )
    except Exception:
        pass


# =========================
# System Notifications Logic (Added for System Manager)
# =========================

@receiver(post_save, sender=SchoolSubscription)
def notify_admin_on_subscription(sender, instance, created, **kwargs):
    """
    إشعار مدير النظام عند إنشاء اشتراك جديد أو تجديده.
    """
    try:
        school_name = getattr(instance.school, "name", "مدرسة")
        plan_name = getattr(instance.plan, "name", "باقة")
        end_date = instance.end_date
        
        if created:
            title = "🔔 طلب اشتراك جديد"
            msg = f"تم تسجيل اشتراك جديد للمدرسة: {school_name}\nالباقة: {plan_name}\nينتهي في: {end_date}"
        else:
            # هنا نفترض الحفظ قد يكون تجديداً أو تعديلاً
            title = "🔔 تحديث اشتراك"
            msg = f"تم تحديث اشتراك المدرسة: {school_name}\nالباقة الحالية: {plan_name}\nتاريخ الانتهاء الجديد: {end_date}"

        # إنشاء الإشعار
        notification = Notification.objects.create(
            title=title,
            message=msg,
            is_important=True,
            # school=None لجعلها عامة نوعاً ما أو نربطها بمستخدمين محددين أدناه
        )
        
        # إرسال لكل من لديه is_superuser=True
        # نفترض أن مدير النظام هو Superuser
        admins = User.objects.filter(is_superuser=True)
        recipients = []
        admin_ids = []
        for admin in admins:
            # تأكد من عدم تكرار الإشعار
            if not NotificationRecipient.objects.filter(notification=notification, teacher=admin).exists():
                recipients.append(NotificationRecipient(
                    notification=notification,
                    teacher=admin
                ))
                admin_ids.append(getattr(admin, "id", None))
        
        if recipients:
            NotificationRecipient.objects.bulk_create(recipients, ignore_conflicts=True)
            if push_new_notification_to_teachers is not None:
                push_new_notification_to_teachers(
                    notification=notification,
                    teacher_ids=[i for i in admin_ids if i],
                )
    except Exception:
        # تجنب كسر العملية الأساسية في حال خطأ في الإشعارات
        pass


@receiver(post_save, sender=Ticket)
def notify_admin_on_platform_ticket(sender, instance, created, **kwargs):
    """
    إشعار مدير النظام عند فتح تذكرة دعم فني (is_platform=True).
    """
    try:
        if created and instance.is_platform:
            title = "🎫 تذكرة دعم فني جديدة"
            creator_name = getattr(instance.creator, "name", str(instance.creator))
            msg = f"قام {creator_name} بفتح تذكرة دعم فني جديدة.\nالعنوان: {instance.title}"
            
            notification = Notification.objects.create(
                title=title,
                message=msg,
                is_important=True
            )
            
            admins = User.objects.filter(is_superuser=True)
            recipients = []
            admin_ids = []
            for admin in admins:
                 if not NotificationRecipient.objects.filter(notification=notification, teacher=admin).exists():
                    recipients.append(NotificationRecipient(
                        notification=notification,
                        teacher=admin
                    ))
                    admin_ids.append(getattr(admin, "id", None))
            
            if recipients:
                NotificationRecipient.objects.bulk_create(recipients, ignore_conflicts=True)
                if push_new_notification_to_teachers is not None:
                    push_new_notification_to_teachers(
                        notification=notification,
                        teacher_ids=[i for i in admin_ids if i],
                    )
    except Exception:
        pass

# ── Cache invalidation signals ──────────────────────────────────────
@receiver(post_save, sender=Report)
def _invalidate_school_on_report(sender, instance, **kwargs):
    """Bust school stats cache when a report is created/updated."""
    try:
        sid = getattr(instance, "school_id", None)
        if sid:
            invalidate_school(sid)
    except Exception:
        pass


@receiver(post_save, sender=Ticket)
def _invalidate_school_on_ticket(sender, instance, **kwargs):
    """Bust school stats cache when a ticket is created/updated."""
    try:
        sid = getattr(instance, "school_id", None)
        if sid:
            invalidate_school(sid)
    except Exception:
        pass


@receiver(post_save, sender=NotificationRecipient)
def _invalidate_user_notif_cache(sender, instance, **kwargs):
    """Bust unread notification count for the recipient."""
    try:
        tid = getattr(instance.teacher, "id", None)
        if tid:
            invalidate_user_notifications(tid)
    except Exception:
        pass