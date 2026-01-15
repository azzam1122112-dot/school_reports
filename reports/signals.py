from __future__ import annotations

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.contrib.auth import get_user_model
from reports.models import Ticket, SchoolSubscription, Notification, NotificationRecipient, School

User = get_user_model()


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
        for admin in admins:
            # تأكد من عدم تكرار الإشعار
            if not NotificationRecipient.objects.filter(notification=notification, teacher=admin).exists():
                recipients.append(NotificationRecipient(
                    notification=notification,
                    teacher=admin
                ))
        
        if recipients:
            NotificationRecipient.objects.bulk_create(recipients, ignore_conflicts=True)
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
            for admin in admins:
                 if not NotificationRecipient.objects.filter(notification=notification, teacher=admin).exists():
                    recipients.append(NotificationRecipient(
                        notification=notification,
                        teacher=admin
                    ))
            
            if recipients:
                NotificationRecipient.objects.bulk_create(recipients, ignore_conflicts=True)
    except Exception:
        pass
