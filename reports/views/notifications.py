# reports/views/notifications.py
# -*- coding: utf-8 -*-
from __future__ import annotations

from ._helpers import *
from ._helpers import (
    _is_staff, _is_staff_or_officer, _is_manager_in_school,
    _role_display_map, _school_manager_label,
    _get_active_school, _canonical_sender_name, _canonical_role_label,
    effective_user_role_label,
)


@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@ratelimit(key="user", rate="10/h", method="POST", block=True)
@require_http_methods(["GET", "POST"])
def notifications_create(request: HttpRequest, mode: str = "notification") -> HttpResponse:
    if NotificationCreateForm is None:
        messages.error(request, "نموذج إنشاء الإشعار غير متوفر.")
        return redirect("reports:home")

    mode = (mode or "notification").strip().lower()
    if mode not in {"notification", "circular"}:
        mode = "notification"
    is_circular = mode == "circular"

    # نربط الإشعارات بمدرسة معيّنة للمدير/الضابط عبر المدرسة النشطة
    active_school = None
    try:
        active_school = _get_active_school(request)
    except Exception:
        active_school = None

    is_superuser = bool(getattr(request.user, "is_superuser", False))
    is_platform = bool(is_platform_admin(request.user)) and not is_superuser

    # حماية: مدير المدرسة/الضابط يحتاج مدرسة نشطة. المشرف العام يختار المدرسة من النموذج.
    if (not is_superuser) and (not is_platform) and active_school is None:
        messages.error(request, "يرجى اختيار المدرسة أولاً قبل إرسال الإشعارات.")
        return redirect("reports:home")

    # التعميمات: مدير المدرسة، مدير النظام، والمشرف العام (ضمن نطاقه فقط).
    if is_circular:
        if not is_superuser and not is_platform:
            if active_school is None or not _is_manager_in_school(request.user, active_school):
                messages.error(request, f"التعاميم متاحة لـ{_school_manager_label(active_school)} فقط.")
                return redirect("reports:home")

    initial = {}
    if request.method == "GET" and is_circular:
        initial["requires_signature"] = True

    form = NotificationCreateForm(
        request.POST or None,
        request.FILES or None,
        user=request.user,
        active_school=active_school,
        initial=initial,
        mode=mode,
    )
    if request.method == "POST":
        if form.is_valid():
            try:
                with transaction.atomic():
                    form.save(
                        creator=request.user,
                        default_school=active_school,
                        force_requires_signature=True if is_circular else False,
                    )
                messages.success(request, "✅ تم إرسال التعميم." if is_circular else "✅ تم إرسال الإشعار.")
                return redirect("reports:circulars_sent" if is_circular else "reports:notifications_sent")
            except Exception:
                logger.exception("notifications_create failed")
                messages.error(request, "تعذّر الإرسال. جرّب لاحقًا.")
        else:
            messages.error(request, "الرجاء تصحيح الأخطاء.")

    return render(
        request,
        "reports/circulars_create.html" if is_circular else "reports/notifications_create.html",
        {
            "form": form,
            "mode": mode,
            "title": "إنشاء تعميم" if is_circular else "إنشاء إشعار",
        },
    )

@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["POST"])
def notification_delete(request: HttpRequest, pk: int) -> HttpResponse:
    if Notification is None:
        messages.error(request, "نموذج الإشعار غير متاح.")
        return redirect("reports:notifications_sent")

    active_school = _get_active_school(request)
    is_superuser = bool(getattr(request.user, "is_superuser", False))
    is_platform = bool(is_platform_admin(request.user)) and not is_superuser
    if (not is_superuser) and (not is_platform) and active_school is None:
        messages.error(request, "يرجى اختيار المدرسة أولاً.")
        return redirect("reports:home")

    n = get_object_or_404(Notification, pk=pk)
    sent_list_url = "reports:circulars_sent" if bool(getattr(n, "requires_signature", False)) else "reports:notifications_sent"

    # التعميمات: سماح لمدير المدرسة/مدير النظام/المشرف العام (ضمن نطاقه)
    if bool(getattr(n, "requires_signature", False)):
        if is_platform:
            if getattr(n, "created_by_id", None) != request.user.id:
                messages.error(request, "لا تملك صلاحية التعامل مع هذا التعميم.")
                return redirect(sent_list_url)
            try:
                if getattr(n, "school", None) is not None and not platform_can_access_school(request.user, getattr(n, "school", None)):
                    messages.error(request, "لا تملك صلاحية التعامل مع تعميم خارج نطاقك.")
                    return redirect(sent_list_url)
            except Exception:
                pass
        elif not is_superuser and not _is_manager_in_school(request.user, active_school):
            messages.error(request, "لا تملك صلاحية التعامل مع التعاميم.")
            return redirect(sent_list_url)
    is_owner = getattr(n, "created_by_id", None) == request.user.id
    is_manager = _is_manager_in_school(request.user, active_school)
    if is_platform:
        if not is_owner:
            messages.error(request, "لا تملك صلاحية حذف هذا الإشعار.")
            return redirect(sent_list_url)
    elif not (is_manager or is_owner):
        messages.error(request, "لا تملك صلاحية حذف هذا الإشعار.")
        return redirect(sent_list_url)

    # عزل حسب المدرسة النشطة (غير السوبر)
    if (not is_superuser) and (not is_platform) and hasattr(n, "school_id"):
        if getattr(n, "school_id", None) is None:
            messages.error(request, "لا تملك صلاحية حذف إشعار عام.")
            return redirect(sent_list_url)
        if getattr(n, "school_id", None) != getattr(active_school, "id", None):
            messages.error(request, "لا تملك صلاحية حذف إشعار من مدرسة أخرى.")
            return redirect(sent_list_url)
    try:
        n.delete()
        messages.success(request, "🗑️ تم حذف الإشعار.")
    except Exception:
        logger.exception("notification_delete failed")
        messages.error(request, "تعذّر حذف الإشعار.")
    return redirect(sent_list_url)

def _recipient_is_read(rec) -> tuple[bool, str | None]:
    for flag in ("is_read", "read", "seen", "opened"):
        if hasattr(rec, flag):
            try:
                return (bool(getattr(rec, flag)), None)
            except Exception:
                pass
    for dt in ("read_at", "seen_at", "opened_at"):
        if hasattr(rec, dt):
            try:
                val = getattr(rec, dt)
                return (bool(val), getattr(val, "strftime", lambda fmt: None)("%Y-%m-%d %H:%M") if val else None)
            except Exception:
                pass
    if hasattr(rec, "status"):
        try:
            st = str(getattr(rec, "status") or "").lower()
            if st in {"read", "seen", "opened", "done"}:
                return (True, None)
        except Exception:
            pass
    return (False, None)

def _arabic_role_label(role_slug: str, active_school: Optional[School] = None) -> str:
    return _role_display_map(active_school).get((role_slug or "").lower(), role_slug or "")


def _digits_only(val: str) -> str:
    return "".join(ch for ch in str(val or "") if ch.isdigit())


def _phone_key(val: str) -> str:
    """Normalize phone for comparison.

    We compare by the last 9 digits to support common Saudi formats:
    - 05xxxxxxxx
    - 5xxxxxxxx
    - 9665xxxxxxxx
    """
    d = _digits_only(val)
    if len(d) >= 9:
        return d[-9:]
    return d


def _mask_phone(val: str) -> str:
    d = _digits_only(val)
    if not d:
        return ""
    if len(d) <= 4:
        return "*" * len(d)
    return ("*" * (len(d) - 4)) + d[-4:]

@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["GET"])
def notification_detail(request: HttpRequest, pk: int) -> HttpResponse:
    if Notification is None:
        messages.error(request, "نموذج الإشعار غير متاح.")
        return redirect("reports:notifications_sent")

    active_school = _get_active_school(request)
    is_superuser = bool(getattr(request.user, "is_superuser", False))
    is_platform = bool(is_platform_admin(request.user)) and not is_superuser
    if (not is_superuser) and (not is_platform) and active_school is None:
        messages.error(request, "يرجى اختيار المدرسة أولاً.")
        return redirect("reports:home")

    n = get_object_or_404(Notification, pk=pk)
    sent_list_url = "reports:circulars_sent" if bool(getattr(n, "requires_signature", False)) else "reports:notifications_sent"

    # التعميمات: سماح لمدير المدرسة/مدير النظام/المشرف العام (ضمن نطاقه)
    if bool(getattr(n, "requires_signature", False)):
        if is_platform:
            if getattr(n, "created_by_id", None) != request.user.id:
                messages.error(request, "لا تملك صلاحية عرض هذا التعميم.")
                return redirect(sent_list_url)
            try:
                if getattr(n, "school", None) is not None and not platform_can_access_school(request.user, getattr(n, "school", None)):
                    messages.error(request, "لا تملك صلاحية عرض تعميم خارج نطاقك.")
                    return redirect(sent_list_url)
            except Exception:
                pass
        elif (not is_superuser) and (not _is_manager_in_school(request.user, active_school)):
            messages.error(request, "لا تملك صلاحية عرض التعاميم.")
            return redirect(sent_list_url)

    # عزل حسب المدرسة النشطة (غير السوبر)
    if (not is_superuser) and (not is_platform) and hasattr(n, "school_id"):
        if getattr(n, "school_id", None) is None:
            messages.error(request, "لا تملك صلاحية عرض إشعار عام.")
            return redirect(sent_list_url)
        if getattr(n, "school_id", None) != getattr(active_school, "id", None):
            messages.error(request, "لا تملك صلاحية عرض إشعار من مدرسة أخرى.")
            return redirect(sent_list_url)

    if not _is_manager_in_school(request.user, active_school):
        if getattr(n, "created_by_id", None) != request.user.id:
            messages.error(request, "لا تملك صلاحية عرض هذا الإشعار.")
            return redirect(sent_list_url)

    body = (
        getattr(n, "message", None) or getattr(n, "body", None) or
        getattr(n, "content", None) or getattr(n, "text", None) or
        getattr(n, "details", None) or ""
    )

    recipients = []
    sig_total = 0
    sig_signed = 0
    if NotificationRecipient is not None:
        # اكتشف اسم FK للإشعار
        notif_fk = None
        for f in NotificationRecipient._meta.get_fields():
            if getattr(getattr(f, "remote_field", None), "model", None) is Notification:
                notif_fk = f.name
                break

        # اسم حقل الشخص
        user_fk = None
        for cand in ("teacher", "user", "recipient"):
            if hasattr(NotificationRecipient, cand):
                user_fk = cand
                break

        if notif_fk:
            qs = NotificationRecipient.objects.filter(**{f"{notif_fk}": n})
            if user_fk:
                qs = qs.select_related(f"{user_fk}", f"{user_fk}__role")
            qs = qs.order_by("id")

            # Batch-prefetch SchoolMembership to avoid N+1 in effective_user_role_label
            recipients_list = list(qs)
            if user_fk and active_school:
                _teachers = [getattr(r, user_fk) for r in recipients_list if getattr(r, user_fk, None)]
                if _teachers:
                    from ..permissions import prefetch_memberships_for_school
                    prefetch_memberships_for_school(_teachers, active_school)

            for r in recipients_list:
                t = getattr(r, user_fk) if user_fk else None
                if not t:
                    continue
                name = getattr(t, "name", None) or getattr(t, "phone", None) or getattr(t, "username", None) or f"مستخدم #{getattr(t, 'pk', '')}"
                role_label = effective_user_role_label(t, active_school=active_school)
                is_read, read_at_str = _recipient_is_read(r)

                signed = bool(getattr(r, "is_signed", False))
                signed_at_str = None
                try:
                    v = getattr(r, "signed_at", None)
                    signed_at_str = v.strftime("%Y-%m-%d %H:%M") if v else None
                except Exception:
                    signed_at_str = None

                if bool(getattr(n, "requires_signature", False)):
                    sig_total += 1
                    if signed:
                        sig_signed += 1

                recipients.append({
                    "name": str(name),
                    "role": role_label,
                    "read": bool(is_read),
                    "read_at": read_at_str,
                    "signed": signed,
                    "signed_at": signed_at_str,
                })

    ctx = {
        "n": n,
        "body": body,
        "recipients": recipients,
        "signature_stats": {
            "total": int(sig_total),
            "signed": int(sig_signed),
            "unsigned": int(max(sig_total - sig_signed, 0)),
        },
    }
    template_name = "reports/circular_detail.html" if bool(getattr(n, "requires_signature", False)) else "reports/notification_detail.html"
    return render(request, template_name, ctx)


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def notification_sign(request: HttpRequest, pk: int) -> HttpResponse:
    """Teacher signs a circular (NotificationRecipient.pk) using phone re-entry + acknowledgement."""
    if NotificationRecipient is None:
        messages.error(request, "نظام الإشعارات غير متاح حالياً.")
        return redirect(request.POST.get("next") or "reports:my_notifications")

    rec = get_object_or_404(
        NotificationRecipient.objects.select_related(
            "notification",
            "notification__created_by",
        ),
        pk=pk,
        teacher=request.user,
    )

    n = getattr(rec, "notification", None)
    if n is None:
        messages.error(request, "تعذّر العثور على التعميم.")
        return redirect("reports:my_circulars")

    if not bool(getattr(n, "requires_signature", False)):
        messages.error(request, "هذا الإشعار لا يتطلب توقيعاً.")
        return redirect("reports:my_notification_detail", pk=rec.pk)

    if bool(getattr(rec, "is_signed", False)):
        messages.info(request, "تم تسجيل توقيعك مسبقاً على هذا التعميم.")
        return redirect("reports:my_circular_detail", pk=rec.pk)

    now = timezone.now()
    max_attempts = 5
    window = timedelta(minutes=15)

    try:
        attempts = int(getattr(rec, "signature_attempt_count", 0) or 0)
    except Exception:
        attempts = 0
    last_attempt = getattr(rec, "signature_last_attempt_at", None)

    # Reset attempts after window
    if last_attempt and (now - last_attempt) > window:
        attempts = 0

    if last_attempt and (now - last_attempt) <= window and attempts >= max_attempts:
        minutes_left = int(max(1, (window - (now - last_attempt)).total_seconds() // 60))
        messages.error(request, f"تم تجاوز عدد المحاولات. حاول مرة أخرى بعد {minutes_left} دقيقة.")
        return redirect("reports:my_circular_detail", pk=rec.pk)

    entered_phone = (request.POST.get("phone") or "").strip()
    ack = request.POST.get("ack") in {"1", "on", "true", "yes"}

    # Register an attempt (best-effort)
    try:
        rec.signature_attempt_count = attempts + 1
        rec.signature_last_attempt_at = now
        rec.save(update_fields=["signature_attempt_count", "signature_last_attempt_at"])
    except Exception:
        pass

    if not ack:
        messages.error(request, "يلزم الموافقة على الإقرار قبل اعتماد التوقيع.")
        return redirect("reports:my_circular_detail", pk=rec.pk)

    if not entered_phone:
        messages.error(request, "يرجى إدخال رقم الجوال المسجل للتوقيع.")
        return redirect("reports:my_circular_detail", pk=rec.pk)

    if _phone_key(entered_phone) != _phone_key(getattr(request.user, "phone", "")):
        messages.error(request, "رقم الجوال غير مطابق للرقم المسجل. تأكد وحاول مرة أخرى.")
        return redirect("reports:my_circular_detail", pk=rec.pk)

    # Sign + mark read
    try:
        update_fields: list[str] = []
        if hasattr(rec, "is_signed"):
            rec.is_signed = True
            update_fields.append("is_signed")
        if hasattr(rec, "signed_at"):
            rec.signed_at = now
            update_fields.append("signed_at")
        if hasattr(rec, "is_read") and not bool(getattr(rec, "is_read", False)):
            rec.is_read = True
            update_fields.append("is_read")
        if hasattr(rec, "read_at") and getattr(rec, "read_at", None) is None:
            rec.read_at = now
            update_fields.append("read_at")
        if update_fields:
            try:
                rec.save(update_fields=update_fields)
            except Exception:
                rec.save()
    except Exception:
        logger.exception("notification_sign failed")
        messages.error(request, "تعذّر تسجيل التوقيع. جرّب لاحقًا.")
        return redirect("reports:my_circular_detail", pk=rec.pk)

    messages.success(request, "✅ تم تسجيل توقيعك على التعميم بنجاح.")
    return redirect("reports:my_circular_detail", pk=rec.pk)


@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["GET"])
def notification_signatures_print(request: HttpRequest, pk: int) -> HttpResponse:
    if Notification is None or NotificationRecipient is None:
        messages.error(request, "نظام الإشعارات غير متاح.")
        return redirect("reports:notifications_sent")

    active_school = _get_active_school(request)
    is_superuser = bool(getattr(request.user, "is_superuser", False))
    is_platform = bool(is_platform_admin(request.user)) and not is_superuser
    if (not is_superuser) and (not is_platform) and active_school is None:
        messages.error(request, "يرجى اختيار المدرسة أولاً.")
        return redirect("reports:home")

    n = get_object_or_404(Notification, pk=pk)
    sent_list_url = "reports:circulars_sent" if bool(getattr(n, "requires_signature", False)) else "reports:notifications_sent"

    # هذا التقرير خاص بالتعاميم فقط
    if not bool(getattr(n, "requires_signature", False)):
        messages.error(request, "هذا التقرير متاح للتعاميم فقط.")
        return redirect(sent_list_url)

    # سماح للمشرف العام بتقارير التعاميم التي أنشأها ضمن نطاقه
    if is_platform:
        if getattr(n, "created_by_id", None) != request.user.id:
            messages.error(request, "لا تملك صلاحية عرض تقرير هذا التعميم.")
            return redirect(sent_list_url)
        try:
            if getattr(n, "school", None) is not None and not platform_can_access_school(request.user, getattr(n, "school", None)):
                messages.error(request, "لا تملك صلاحية عرض تعميم خارج نطاقك.")
                return redirect(sent_list_url)
        except Exception:
            pass

    # Permission: manager in school or creator
    if (not is_platform) and (not _is_manager_in_school(request.user, active_school)):
        if getattr(n, "created_by_id", None) != request.user.id:
            messages.error(request, "لا تملك صلاحية عرض تقرير هذا التعميم.")
            return redirect(sent_list_url)

    # School isolation
    if (not is_superuser) and (not is_platform) and hasattr(n, "school_id"):
        if getattr(n, "school_id", None) != getattr(active_school, "id", None):
            messages.error(request, "لا تملك صلاحية عرض تعميم من مدرسة أخرى.")
            return redirect(sent_list_url)

    qs = (
        NotificationRecipient.objects
        .filter(notification=n)
        .select_related("teacher", "teacher__role")
        .order_by("teacher__name", "id")
    )

    # Batch-prefetch memberships to avoid N+1 in effective_user_role_label
    recipients_list = list(qs)
    if active_school:
        _teachers = [getattr(r, "teacher") for r in recipients_list if getattr(r, "teacher", None)]
        if _teachers:
            from ..permissions import prefetch_memberships_for_school
            prefetch_memberships_for_school(_teachers, active_school)

    rows = []
    signed = 0
    total = 0
    for r in recipients_list:
        t = getattr(r, "teacher", None)
        if not t:
            continue
        total += 1
        is_signed = bool(getattr(r, "is_signed", False))
        if is_signed:
            signed += 1
        rows.append({
            "name": getattr(t, "name", "") or str(t),
            "role": effective_user_role_label(t, active_school=active_school),
            "phone": _mask_phone(getattr(t, "phone", "")),
            "read": bool(getattr(r, "is_read", False)),
            "read_at": getattr(r, "read_at", None),
            "signed": is_signed,
            "signed_at": getattr(r, "signed_at", None),
        })

    ctx = {
        "n": n,
        "rows": rows,
        "stats": {"total": total, "signed": signed, "unsigned": max(total - signed, 0)},
    }
    return render(request, "reports/notification_signatures_print.html", ctx)


@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["GET"])
def notification_signatures_csv(request: HttpRequest, pk: int) -> HttpResponse:
    if Notification is None or NotificationRecipient is None:
        return HttpResponse("unavailable", status=400)

    active_school = _get_active_school(request)
    is_superuser = bool(getattr(request.user, "is_superuser", False))
    is_platform = bool(is_platform_admin(request.user)) and not is_superuser
    if (not is_superuser) and (not is_platform) and active_school is None:
        return HttpResponse("active_school_required", status=403)

    n = get_object_or_404(Notification, pk=pk)

    if not bool(getattr(n, "requires_signature", False)):
        return HttpResponse("forbidden", status=403)

    if is_platform:
        if getattr(n, "created_by_id", None) != request.user.id:
            return HttpResponse("forbidden", status=403)
        try:
            if getattr(n, "school", None) is not None and not platform_can_access_school(request.user, getattr(n, "school", None)):
                return HttpResponse("forbidden", status=403)
        except Exception:
            pass

    if (not is_platform) and (not _is_manager_in_school(request.user, active_school)):
        if getattr(n, "created_by_id", None) != request.user.id:
            return HttpResponse("forbidden", status=403)

    if (not is_superuser) and (not is_platform) and hasattr(n, "school_id"):
        if getattr(n, "school_id", None) != getattr(active_school, "id", None):
            return HttpResponse("forbidden", status=403)

    import csv
    from io import StringIO

    out = StringIO()
    writer = csv.writer(out)
    writer.writerow([
        "الاسم",
        "الدور",
        "الجوال (مخفي)",
        "الحالة (مقروء)",
        "وقت القراءة",
        "الحالة (موقّع)",
        "وقت التوقيع",
    ])

    qs = (
        NotificationRecipient.objects
        .filter(notification=n)
        .select_related("teacher", "teacher__role")
        .order_by("teacher__name", "id")
    )

    # Batch-prefetch memberships to avoid N+1
    recipients_list = list(qs)
    if active_school:
        _teachers = [getattr(r, "teacher") for r in recipients_list if getattr(r, "teacher", None)]
        if _teachers:
            from ..permissions import prefetch_memberships_for_school
            prefetch_memberships_for_school(_teachers, active_school)

    for r in recipients_list:
        t = getattr(r, "teacher", None)
        if not t:
            continue
        role_label = effective_user_role_label(t, active_school=active_school)
        writer.writerow([
            getattr(t, "name", "") or str(t),
            role_label,
            _mask_phone(getattr(t, "phone", "")),
            "نعم" if bool(getattr(r, "is_read", False)) else "لا",
            getattr(getattr(r, "read_at", None), "strftime", lambda fmt: "")("%Y-%m-%d %H:%M") if getattr(r, "read_at", None) else "",
            "نعم" if bool(getattr(r, "is_signed", False)) else "لا",
            getattr(getattr(r, "signed_at", None), "strftime", lambda fmt: "")("%Y-%m-%d %H:%M") if getattr(r, "signed_at", None) else "",
        ])

    resp = HttpResponse(out.getvalue(), content_type="text/csv; charset=utf-8")
    safe_title = (getattr(n, "title", "") or "notification").strip().replace("\n", " ").replace("\r", " ")
    resp["Content-Disposition"] = f'attachment; filename="signatures_{pk}_{safe_title[:40]}.csv"'
    return resp

@require_http_methods(["GET"])
def unread_notifications_count(request: HttpRequest) -> HttpResponse:
    """إرجاع عدد الإشعارات غير المقروءة بتنسيق JSON لاستخدامه في الـ Polling.

    ملاحظة: لا نُعيد توجيه المستخدمين غير المسجلين لصفحة الدخول لأن هذا المسار يُستدعى بشكل دوري
    من الواجهة (Polling)، وإعادة التوجيه قد تسبب ضغطاً وتداخل مع RateLimit.
    """
    if not getattr(request.user, "is_authenticated", False):
        return JsonResponse({"count": 0, "authenticated": False})

    if NotificationRecipient is None:
        return JsonResponse({"count": 0, "unread": 0, "signatures_pending": 0, "authenticated": True})

    # Short-TTL cache per user + school to cut repeated aggregate queries.
    try:
        ttl = int(getattr(settings, "UNREAD_COUNT_CACHE_TTL_SECONDS", 15) or 0)
    except Exception:
        ttl = 15

    cache_key = None
    if ttl > 0:
        try:
            sid_raw = request.session.get("active_school_id")
            sid_for_key = str(int(sid_raw)) if sid_raw else "none"
        except Exception:
            sid_for_key = "none"
        try:
            uid = int(getattr(request.user, "id", 0) or 0)
            cache_key = f"unreadcnt:v1:u{uid}:s{sid_for_key}"
            cached = cache.get(cache_key)
            if isinstance(cached, dict):
                return JsonResponse(cached)
        except Exception:
            cache_key = None

    active_school = _get_active_school(request)
    now = timezone.now()

    qs = NotificationRecipient.objects.filter(teacher=request.user)

    # عزل حسب المدرسة النشطة (مع السماح بإشعارات عامة school=NULL)
    try:
        if active_school is not None and Notification is not None and hasattr(Notification, "school"):
            qs = qs.filter(Q(notification__school=active_school) | Q(notification__school__isnull=True))
    except Exception:
        pass

    # استبعاد المنتهي
    try:
        if Notification is not None and hasattr(Notification, "expires_at"):
            qs = qs.filter(Q(notification__expires_at__gt=now) | Q(notification__expires_at__isnull=True))
    except Exception:
        pass

    # unread = unread notifications only (exclude circulars)
    unread_q = Q(is_read=False)
    try:
        if Notification is not None and hasattr(Notification, "requires_signature"):
            unread_q &= Q(notification__requires_signature=False)
    except Exception:
        pass

    # signatures_pending = unsigned circulars
    pending_sig_q = Q(pk__in=[])
    try:
        if Notification is not None and hasattr(Notification, "requires_signature") and hasattr(NotificationRecipient, "is_signed"):
            pending_sig_q = Q(notification__requires_signature=True, is_signed=False)
    except Exception:
        pending_sig_q = Q(pk__in=[])

    # count = items needing attention (backward compatible): unread notifications OR pending circular signatures
    attention_q = unread_q | pending_sig_q

    agg = qs.aggregate(
        count=Count("id", filter=attention_q),
        unread=Count("id", filter=unread_q),
        signatures_pending=Count("id", filter=pending_sig_q),
    )

    payload = {
        "count": int(agg.get("count") or 0),
        "unread": int(agg.get("unread") or 0),
        "signatures_pending": int(agg.get("signatures_pending") or 0),
        "authenticated": True,
    }

    if cache_key and ttl > 0:
        try:
            cache.set(cache_key, payload, ttl)
        except Exception:
            pass

    return JsonResponse(payload)

@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_notifications(request: HttpRequest) -> HttpResponse:
    if NotificationRecipient is None:
        return render(request, "reports/my_notifications.html", {"page_obj": Paginator([], 12).get_page(1)})

    active_school = _get_active_school(request)

    qs = (
        NotificationRecipient.objects
        .select_related("notification", "notification__created_by", "notification__created_by__role")
        .filter(teacher=request.user)
        .order_by("-created_at", "-id")
    )

    # فصل: هذه الصفحة للإشعارات فقط (بدون التعاميم)
    try:
        if Notification is not None and hasattr(Notification, "requires_signature"):
            qs = qs.filter(notification__requires_signature=False)
    except Exception:
        pass

    # عزل حسب المدرسة النشطة (مع السماح بإشعارات عامة school=NULL)
    try:
        if active_school is not None and Notification is not None and hasattr(Notification, "school"):
            qs = qs.filter(Q(notification__school=active_school) | Q(notification__school__isnull=True))
    except Exception:
        pass

    # إخفاء المنتهية بحسب الحقول المتاحة
    now = timezone.now()
    try:
        if Notification is not None and hasattr(Notification, "expires_at"):
            qs = qs.exclude(notification__expires_at__lt=now)
        elif Notification is not None and hasattr(Notification, "ends_at"):
            qs = qs.exclude(notification__ends_at__lt=now)
    except Exception:
        pass

    page = Paginator(qs, 12).get_page(request.GET.get("page") or 1)

    # عند فتح تبويب "إشعاراتي" غالباً يتوقع المستخدم أن تصبح الإشعارات المعروضة كمقروءة.
    # لا يمكن الاعتماد على "إغلاق التبويب" كإشارة مؤكدة من المتصفح، لذا نُحدّثها هنا.
    try:
        items = list(page.object_list)
        unread_ids = [x.pk for x in items if hasattr(x, "is_read") and not bool(getattr(x, "is_read", False))]
        if unread_ids:
            now = timezone.now()
            fields = {f.name for f in NotificationRecipient._meta.get_fields()}
            upd: dict = {}
            if "is_read" in fields:
                upd["is_read"] = True
            if "read_at" in fields:
                upd["read_at"] = now
            if upd:
                NotificationRecipient.objects.filter(pk__in=unread_ids, teacher=request.user).update(**upd)

                # Bulk update won't trigger post_save; request a one-off WS resync.
                try:
                    from ..realtime_notifications import push_force_resync

                    push_force_resync(teacher_id=int(getattr(request.user, "id", 0) or 0))
                except Exception:
                    pass

                for x in items:
                    if x.pk in unread_ids:
                        if "is_read" in upd:
                            setattr(x, "is_read", True)
                        if "read_at" in upd:
                            setattr(x, "read_at", now)
            page.object_list = items
    except Exception:
        pass

    # اسم المرسل + الدور الصحيح (مُوحّد)
    try:
        items = list(page.object_list)
        for rr in items:
            n = getattr(rr, "notification", None)
            sender = getattr(n, "created_by", None) if n is not None else None
            school_scope = (getattr(n, "school", None) if n is not None else None) or active_school
            rr.sender_name = _canonical_sender_name(sender)
            rr.sender_role_label = _canonical_role_label(sender, school_scope)
        page.object_list = items
    except Exception:
        pass
    return render(request, "reports/my_notifications.html", {"page_obj": page})


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_circulars(request: HttpRequest) -> HttpResponse:
    """قائمة التعاميم للمستخدم (التي تتطلب توقيعاً)."""
    if NotificationRecipient is None:
        return render(request, "reports/my_circulars.html", {"page_obj": Paginator([], 12).get_page(1)})

    active_school = _get_active_school(request)

    try:
        qs = (
            NotificationRecipient.objects
            .select_related("notification")
            .filter(teacher=request.user)
            .order_by("-created_at", "-id")
        )
    except Exception:
        logger.exception("my_circulars: failed to build base queryset")
        messages.error(request, "تعذر تحميل التعاميم حالياً. سيتم تسجيل المشكلة تلقائياً.")
        return render(request, "reports/my_circulars.html", {"page_obj": Paginator([], 12).get_page(1)})

    # فصل: هذه الصفحة للتعاميم فقط
    try:
        if Notification is not None and hasattr(Notification, "requires_signature"):
            qs = qs.filter(notification__requires_signature=True)
    except Exception:
        pass

    # عزل حسب المدرسة النشطة (مع السماح بإشعارات عامة school=NULL)
    try:
        if active_school is not None and Notification is not None and hasattr(Notification, "school"):
            qs = qs.filter(Q(notification__school=active_school) | Q(notification__school__isnull=True))
    except Exception:
        pass

    # إخفاء المنتهية بحسب الحقول المتاحة
    now = timezone.now()
    try:
        if Notification is not None and hasattr(Notification, "expires_at"):
            qs = qs.exclude(notification__expires_at__lt=now)
        elif Notification is not None and hasattr(Notification, "ends_at"):
            qs = qs.exclude(notification__ends_at__lt=now)
    except Exception:
        pass

    try:
        page = Paginator(qs, 12).get_page(request.GET.get("page") or 1)
    except Exception:
        logger.exception("my_circulars: failed to paginate")
        messages.error(request, "تعذر تحميل التعاميم حالياً. سيتم تسجيل المشكلة تلقائياً.")
        return render(request, "reports/my_circulars.html", {"page_obj": Paginator([], 12).get_page(1)})

    # مهم: QuerySet داخل Page قد يبقى كسولاً، وقد يحدث الخطأ أثناء عرض القالب.
    # هنا نجبر التقييم داخل الـ view حتى نلتقط أخطاء قاعدة البيانات (مثل نقص migrations) ونمنع 500.
    try:
        page.object_list = list(page.object_list)
    except Exception:
        logger.exception("my_circulars: failed to evaluate page object_list")
        messages.error(request, "تعذر تحميل التعاميم حالياً. سيتم تسجيل المشكلة تلقائياً.")
        return render(request, "reports/my_circulars.html", {"page_obj": Paginator([], 12).get_page(1)})

    # عند فتح تبويب "تعاميمي" غالباً يتوقع المستخدم أن تصبح العناصر المعروضة كمقروءة.
    try:
        items = list(page.object_list)
        unread_ids = [x.pk for x in items if hasattr(x, "is_read") and not bool(getattr(x, "is_read", False))]
        if unread_ids:
            now = timezone.now()
            fields = {f.name for f in NotificationRecipient._meta.get_fields()}
            upd: dict = {}
            if "is_read" in fields:
                upd["is_read"] = True
            if "read_at" in fields:
                upd["read_at"] = now
            if upd:
                NotificationRecipient.objects.filter(pk__in=unread_ids, teacher=request.user).update(**upd)
                for x in items:
                    if x.pk in unread_ids:
                        if "is_read" in upd:
                            setattr(x, "is_read", True)
                        if "read_at" in upd:
                            setattr(x, "read_at", now)
            page.object_list = items
    except Exception:
        pass

    return render(request, "reports/my_circulars.html", {"page_obj": page})


@login_required(login_url="reports:login")
@require_http_methods(["GET"])
def my_notification_detail(request: HttpRequest, pk: int) -> HttpResponse:
    """Show a single notification (for the current user) in a dedicated page.

    pk here refers to NotificationRecipient.pk.
    """
    if NotificationRecipient is None:
        messages.error(request, "نموذج الإشعار غير متاح.")
        return redirect("reports:my_notifications")

    try:
        r = get_object_or_404(
            NotificationRecipient.objects.select_related(
                "notification",
                "notification__created_by",
                "notification__created_by__role",
            ),
            pk=pk,
            teacher=request.user,
        )
    except Exception:
        logger.exception("my_notification_detail: failed to load recipient row", extra={"pk": pk})
        messages.error(request, "تعذر فتح التعميم/الإشعار حالياً. سيتم تسجيل المشكلة تلقائياً.")
        return redirect("reports:my_circulars")

    n = getattr(r, "notification", None)
    if n is None:
        messages.error(request, "تعذّر العثور على الإشعار.")
        return redirect("reports:my_notifications")

    is_circular = bool(getattr(n, "requires_signature", False))

    # منع الخلط 100%: إذا كان الرابط من تبويب خاطئ نعيد توجيهه للرابط الصحيح
    try:
        url_name = getattr(getattr(request, "resolver_match", None), "url_name", "") or ""
        if is_circular and url_name == "my_notification_detail":
            return redirect("reports:my_circular_detail", pk=r.pk)
        if (not is_circular) and url_name == "my_circular_detail":
            return redirect("reports:my_notification_detail", pk=r.pk)
    except Exception:
        pass

    body = (
        getattr(n, "message", None)
        or getattr(n, "body", None)
        or getattr(n, "content", None)
        or getattr(n, "text", None)
        or getattr(n, "details", None)
        or ""
    )

    # اسم/دور المرسل (موحّد)
    try:
        sender = getattr(n, "created_by", None)
        school_scope = getattr(n, "school", None) or _get_active_school(request)
        sender_name = _canonical_sender_name(sender)
        sender_role_label = _canonical_role_label(sender, school_scope)
    except Exception:
        sender_name = "الإدارة"
        sender_role_label = ""

    # Mark as read on open (best-effort, supports different schemas)
    try:
        updated_fields: list[str] = []
        if hasattr(r, "is_read") and not bool(getattr(r, "is_read", False)):
            setattr(r, "is_read", True)
            updated_fields.append("is_read")
        if hasattr(r, "read_at") and getattr(r, "read_at", None) is None:
            setattr(r, "read_at", timezone.now())
            updated_fields.append("read_at")
        if updated_fields:
            try:
                r.save(update_fields=updated_fields)
            except Exception:
                r.save()
    except Exception:
        pass

    return render(
        request,
        "reports/my_circular_detail.html" if is_circular else "reports/my_notification_detail.html",
        {
            "r": r,
            "n": n,
            "body": body,
            "sender_name": sender_name,
            "sender_role_label": sender_role_label,
        },
    )

@login_required(login_url="reports:login")
@user_passes_test(_is_staff_or_officer, login_url="reports:login")
@require_http_methods(["GET"])
def notifications_sent(request: HttpRequest, mode: str = "notification") -> HttpResponse:
    mode = (mode or "notification").strip().lower()
    if mode not in {"notification", "circular"}:
        mode = "notification"
    is_circular = mode == "circular"

    is_platform = bool(is_platform_admin(request.user)) and not bool(getattr(request.user, "is_superuser", False))

    if is_circular:
        if not request.user.is_superuser and not is_platform and not _is_manager_in_school(request.user, _get_active_school(request)):
            active_school = _get_active_school(request)
            messages.error(request, f"التعاميم متاحة لـ{_school_manager_label(active_school)} فقط.")
            return redirect("reports:home")

    if Notification is None:
        return render(
            request,
            "reports/circulars_sent.html" if is_circular else "reports/notifications_sent.html",
            {
                "page_obj": Paginator([], 20).get_page(1),
                "stats": {},
                "mode": mode,
                "title": "التعاميم المرسلة" if is_circular else "الإشعارات المرسلة",
            },
        )

    active_school = _get_active_school(request)
    if not request.user.is_superuser and (not is_platform) and active_school is None:
        messages.error(request, "يرجى اختيار المدرسة أولاً.")
        return redirect("reports:home")

    qs = Notification.objects.all().order_by("-created_at", "-id")

    # صفحة "المرسلة" تعرض فقط الإشعارات التي أرسلها مستخدم فعلياً.
    # إشعارات النظام (created_by=NULL) مثل التعليقات الخاصة والتنبيهات الآلية لا تظهر هنا.
    try:
        if hasattr(Notification, "created_by"):
            qs = qs.filter(created_by__isnull=False)
    except Exception:
        pass

    # فصل التعاميم عن الإشعارات
    try:
        if hasattr(Notification, "requires_signature"):
            qs = qs.filter(requires_signature=True) if is_circular else qs.filter(requires_signature=False)
    except Exception:
        pass

    # غير السوبر: لا يرى إلا إشعارات المدرسة النشطة (لا إشعارات عامة)
    try:
        if (not request.user.is_superuser) and (not is_platform) and hasattr(Notification, "school"):
            qs = qs.filter(school=active_school)
    except Exception:
        pass

    # المشرف العام: يرى فقط ما قام بإرساله، وبحد نطاقه إن كانت المدرسة محددة
    if is_platform:
        qs = qs.filter(created_by=request.user)
        try:
            if hasattr(Notification, "school"):
                qs = qs.filter(Q(school__isnull=True) | Q(school__in=platform_allowed_schools_qs(request.user)))
        except Exception:
            pass

    # ✅ صفحة "المرسلة" يجب أن تُظهر ما أرسله المستخدم الحالي فقط
    # (مدير المدرسة كان يرى سابقًا جميع إشعارات المدرسة بما فيها إشعارات المشرفين)
    if not request.user.is_superuser:
        qs = qs.filter(created_by=request.user)

    qs = qs.select_related("created_by")
    page = Paginator(qs, 20).get_page(request.GET.get("page") or 1)

    notif_ids = [n.id for n in page.object_list]
    stats: dict[int, dict] = {}

    # حساب read/total بمرونة على NotificationRecipient
    if NotificationRecipient is not None and notif_ids:
        notif_fk_name = None
        try:
            for f in NotificationRecipient._meta.get_fields():
                if getattr(getattr(f, "remote_field", None), "model", None) is Notification:
                    notif_fk_name = f.name
                    break
        except Exception:
            notif_fk_name = None

        if notif_fk_name:
            fields = {f.name for f in NotificationRecipient._meta.get_fields()}
            if "is_read" in fields:
                read_filter = Q(is_read=True)
            elif "read_at" in fields:
                read_filter = Q(read_at__isnull=False)
            elif "seen_at" in fields:
                read_filter = Q(seen_at__isnull=False)
            elif "status" in fields:
                read_filter = Q(status__in=["read", "seen", "opened", "done"])
            else:
                read_filter = Q(pk__in=[])

            fields = {f.name for f in NotificationRecipient._meta.get_fields()}
            signed_filter = None
            if "is_signed" in fields:
                signed_filter = Q(is_signed=True)
            elif "signed_at" in fields:
                signed_filter = Q(signed_at__isnull=False)

            ann = {
                "total": Count("id"),
                "read": Count("id", filter=read_filter),
            }
            if signed_filter is not None:
                ann["signed"] = Count("id", filter=signed_filter)

            rc = (
                NotificationRecipient.objects
                .filter(**{f"{notif_fk_name}_id__in": notif_ids})
                .values(f"{notif_fk_name}_id")
                .annotate(**ann)
            )
            for row in rc:
                stats[row[f"{notif_fk_name}_id"]] = {
                    "total": row.get("total", 0),
                    "read": row.get("read", 0),
                    "signed": row.get("signed", 0),
                }

    # أسماء مستلمين مختصرة
    rec_names_map: dict[int, list[str]] = {i: [] for i in notif_ids}

    def _name_of(person) -> str:
        return (getattr(person, "name", None) or
                getattr(person, "phone", None) or
                getattr(person, "username", None) or
                getattr(person, "national_id", None) or
                str(person))

    for n in page.object_list:
        names_set = set()
        try:
            rel = getattr(n, "recipients", None)
            if rel is not None:
                for t in rel.all()[:12]:
                    if t:
                        nm = _name_of(t)
                        if nm not in names_set:
                            names_set.add(nm)
        except Exception:
            pass
        rec_names_map[n.id] = list(names_set)

    remaining_ids = [nid for nid, arr in rec_names_map.items() if len(arr) < 5]
    if remaining_ids and NotificationRecipient is not None:
        notif_fk_name = None
        try:
            for f in NotificationRecipient._meta.get_fields():
                if getattr(getattr(f, "remote_field", None), "model", None) is Notification:
                    notif_fk_name = f.name
                    break
        except Exception:
            pass

        if notif_fk_name:
            thr_qs = NotificationRecipient.objects.filter(**{f"{notif_fk_name}_id__in": remaining_ids})
            for r in thr_qs:
                nid = getattr(r, f"{notif_fk_name}_id", None)
                if not nid:
                    continue
                person = (getattr(r, "teacher", None) or
                          getattr(r, "user", None) or
                          getattr(r, "recipient", None))
                if person:
                    nm = _name_of(person)
                    arr = rec_names_map.get(nid, [])
                    if nm and nm not in arr and len(arr) < 12:
                        arr.append(nm)
                        rec_names_map[nid] = arr

    for n in page.object_list:
        n.rec_names = rec_names_map.get(n.id, [])

    return render(
        request,
        "reports/circulars_sent.html" if is_circular else "reports/notifications_sent.html",
        {
            "page_obj": page,
            "stats": stats,
            "mode": mode,
            "title": "التعاميم المرسلة" if is_circular else "الإشعارات المرسلة",
        },
    )

# تعليم الإشعار كمقروء (حسب Recipient pk)
@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def notification_mark_read(request: HttpRequest, pk: int) -> HttpResponse:
    if NotificationRecipient is None:
        return redirect(request.POST.get("next") or "reports:my_notifications")
    item = get_object_or_404(NotificationRecipient, pk=pk, teacher=request.user)
    if not getattr(item, "is_read", False):
        if hasattr(item, "is_read"):
            item.is_read = True
        if hasattr(item, "read_at"):
            item.read_at = timezone.now()
        try:
            if hasattr(item, "is_read") and hasattr(item, "read_at"):
                item.save(update_fields=["is_read", "read_at"])
            else:
                item.save()
        except Exception:
            item.save()
    return redirect(request.POST.get("next") or "reports:my_notifications")

# تحديد الكل كمقروء
@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def notifications_mark_all_read(request: HttpRequest) -> HttpResponse:
    if NotificationRecipient is None:
        return redirect(request.POST.get("next") or "reports:my_notifications")
    qs = NotificationRecipient.objects.filter(teacher=request.user)

    # فصل: هذا الإجراء خاص بالإشعارات فقط (يستبعد التعاميم)
    try:
        if Notification is not None and hasattr(Notification, "requires_signature"):
            qs = qs.filter(notification__requires_signature=False)
    except Exception:
        pass
    try:
        if "is_read" in {f.name for f in NotificationRecipient._meta.get_fields()}:
            qs = qs.filter(is_read=False)
            qs.update(is_read=True, read_at=timezone.now() if hasattr(NotificationRecipient, "read_at") else None)
        elif "read_at" in {f.name for f in NotificationRecipient._meta.get_fields()}:
            qs = qs.filter(read_at__isnull=True)
            qs.update(read_at=timezone.now())
        else:
            pass
    except Exception:
        for x in qs:
            try:
                if hasattr(x, "is_read"):
                    x.is_read = True
                if hasattr(x, "read_at"):
                    x.read_at = timezone.now()
                x.save()
            except Exception:
                continue
    messages.success(request, "تم تحديد جميع الإشعارات كمقروءة.")

    # Bulk update won't trigger signals; ask clients to resync once.
    try:
        from ..realtime_notifications import push_force_resync

        push_force_resync(teacher_id=int(getattr(request.user, "id", 0) or 0))
    except Exception:
        pass

    return redirect(request.POST.get("next") or "reports:my_notifications")


@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def circulars_mark_all_read(request: HttpRequest) -> HttpResponse:
    if NotificationRecipient is None:
        return redirect(request.POST.get("next") or "reports:my_circulars")

    qs = NotificationRecipient.objects.filter(teacher=request.user)
    try:
        if Notification is not None and hasattr(Notification, "requires_signature"):
            qs = qs.filter(notification__requires_signature=True)
    except Exception:
        pass

    try:
        if "is_read" in {f.name for f in NotificationRecipient._meta.get_fields()}:
            qs = qs.filter(is_read=False)
            qs.update(is_read=True, read_at=timezone.now() if hasattr(NotificationRecipient, "read_at") else None)
        elif "read_at" in {f.name for f in NotificationRecipient._meta.get_fields()}:
            qs = qs.filter(read_at__isnull=True)
            qs.update(read_at=timezone.now())
    except Exception:
        for x in qs:
            try:
                if hasattr(x, "is_read"):
                    x.is_read = True
                if hasattr(x, "read_at"):
                    x.read_at = timezone.now()
                x.save()
            except Exception:
                continue

    messages.success(request, "تم تحديد جميع التعاميم كمقروءة.")
    return redirect(request.POST.get("next") or "reports:my_circulars")

# تعليم الإشعار كمقروء (حسب رقم الإشعار نفسه لا الـRecipient)
@login_required(login_url="reports:login")
@require_http_methods(["POST"])
def notification_mark_read_by_notification(request: HttpRequest, pk: int) -> HttpResponse:
    if NotificationRecipient is None:
        return JsonResponse({"ok": False}, status=400)
    try:
        item = NotificationRecipient.objects.filter(
            notification_id=pk, teacher=request.user
        ).first()
        if item:
            if hasattr(item, "is_read") and not item.is_read:
                item.is_read = True
            if hasattr(item, "read_at") and getattr(item, "read_at", None) is None:
                item.read_at = timezone.now()
            try:
                if hasattr(item, "is_read") and hasattr(item, "read_at"):
                    item.save(update_fields=["is_read", "read_at"])
                else:
                    item.save()
            except Exception:
                item.save()
        return JsonResponse({"ok": True})
    except Exception:
        return JsonResponse({"ok": False}, status=400)

# إبقاء المسار القديم للتوافق الخلفي: تحويل إلى صفحة الإنشاء
@login_required(login_url="reports:login")
@user_passes_test(_is_staff, login_url="reports:login")
def send_notification(request: HttpRequest) -> HttpResponse:
    return redirect("reports:notifications_create")
