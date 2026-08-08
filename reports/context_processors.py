# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Dict, Any, List, Iterable, Optional, Tuple, Set
from datetime import timedelta
import hashlib

from django.conf import settings
from django.core.cache import cache
from django.http import HttpRequest
from django.utils import timezone
from django.apps import apps
from django.db.models import Count, Q
from django.urls import reverse

from .models import (
    Ticket,
    Department,
    Report,
    School,
    SchoolYearArchive,
    school_has_archive_addon,
)
from . import capabilities as caps
from .permissions import effective_user_role_label, get_school_manager_school_ids
from .gender_labels import school_gender_labels, school_gender_template_context

# حالات التذاكر
OPEN_STATES = {"open", "new"}
INPROGRESS_STATES = {"in_progress", "pending"}
UNRESOLVED_STATES = OPEN_STATES | INPROGRESS_STATES
CLOSED_STATES = {"done", "rejected", "cancelled"}


# -----------------------------
# أدوات مساعدة عامة
# -----------------------------
def _safe_count(qs) -> int:
    try:
        return qs.count()
    except Exception:
        return 0


def _get_model(app_label: str, model_name: str):
    try:
        return apps.get_model(app_label, model_name)
    except Exception:
        return None


def _get_membership_model():
    return _get_model("reports", "DepartmentMembership")


def _model_fields(model) -> Set[str]:
    try:
        return {f.name for f in model._meta.get_fields()}
    except Exception:
        return set()


def _nav_cache_ttl_seconds() -> int:
    """Short TTL to smooth load spikes without keeping stale nav data for long."""
    try:
        default_ttl = 20 if not bool(getattr(settings, "DEBUG", False)) else 5
        return max(0, int(getattr(settings, "NAV_CONTEXT_CACHE_TTL_SECONDS", default_ttl) or 0))
    except Exception:
        return 10


def _active_school_id_from_request(request: Optional[HttpRequest]) -> int:
    try:
        sid = request.session.get("active_school_id") if request is not None else None
        return int(sid) if sid else 0
    except Exception:
        return 0


# -----------------------------
# كشف أقسام المسؤول Officer
# -----------------------------
def _officer_role_values(membership_model) -> Iterable:
    values = set()
    if membership_model is None:
        return {"officer", 1, "1"}
    v = getattr(membership_model, "OFFICER", None)
    if v is not None:
        values.add(v)
    RoleType = getattr(membership_model, "RoleType", None)
    if RoleType is not None:
        v = getattr(RoleType, "OFFICER", None)
        if v is not None:
            values.add(v)
    # fallback
    values.update({"officer", 1, "1"})
    return values


def _teacher_role_values(membership_model) -> Iterable:
    values = set()
    if membership_model is None:
        return {"teacher", 0, "0"}
    v = getattr(membership_model, "TEACHER", None)
    if v is not None:
        values.add(v)
    RoleType = getattr(membership_model, "RoleType", None)
    if RoleType is not None:
        v = getattr(RoleType, "TEACHER", None)
        if v is not None:
            values.add(v)
    # fallback
    values.update({"teacher", 0, "0"})
    return values


def _detect_officer_departments(user, active_school: Optional[School] = None) -> List[Department]:
    """أقسام المسؤول/رئيس القسم (OFFICER) لعرض تقارير القسم."""
    Membership = _get_membership_model()
    if Membership is None:
        return []
    try:
        officer_values = list(_officer_role_values(Membership))
        membs = (
            Membership.objects.select_related("department")
            .filter(teacher=user, role_type__in=officer_values, department__is_active=True)
        )
        # عزل حسب المدرسة النشطة إن وُجدت
        try:
            if active_school is not None and "school" in _model_fields(Department):
                membs = membs.filter(department__school=active_school)
        except Exception:
            pass
        seen, unique = set(), []
        for m in membs:
            d = m.department
            if d and d.pk not in seen:
                seen.add(d.pk)
                unique.append(d)
        return unique
    except Exception:
        return []


def _detect_member_departments(user, active_school: Optional[School] = None) -> List[Department]:
    """أقسام العضو (TEACHER) لعرض/طباعة تقارير القسم فقط."""
    Membership = _get_membership_model()
    if Membership is None:
        return []
    try:
        teacher_values = list(_teacher_role_values(Membership))
        membs = (
            Membership.objects.select_related("department")
            .filter(teacher=user, role_type__in=teacher_values, department__is_active=True)
        )
        # عزل حسب المدرسة النشطة إن وُجدت
        try:
            if active_school is not None and "school" in _model_fields(Department):
                membs = membs.filter(department__school=active_school)
        except Exception:
            pass

        seen, unique = set(), []
        for m in membs:
            d = m.department
            if d and d.pk not in seen:
                seen.add(d.pk)
                unique.append(d)
        return unique
    except Exception:
        return []


def _user_department_codes(user, active_school: Optional[School] = None) -> List[str]:
    Membership = _get_membership_model()
    if Membership is None:
        return []
    try:
        qs = Membership.objects.filter(teacher=user, department__is_active=True)
        try:
            if active_school is not None and "school" in _model_fields(Department):
                qs = qs.filter(department__school=active_school)
        except Exception:
            pass
        codes = list(qs.values_list("department__slug", flat=True))
        return [c for c in codes if c]
    except Exception:
        return []


# -----------------------------
# نماذج الإشعارات (ديناميكيًا)
# -----------------------------
def _notification_models():
    """يُعيد موديل الإشعار + موديل سجل الاستلام/القراءة إن وُجد."""
    N = (
        _get_model("reports", "Notification")
        or _get_model("reports", "Announcement")
        or _get_model("reports", "AdminMessage")
    )
    # ✅ دعم اسم NotificationRecipient (المعمول به في مشروعك)
    R = (
        _get_model("reports", "NotificationRecipient")
        or _get_model("reports", "NotificationRead")
        or _get_model("reports", "NotificationReceipt")
        or _get_model("reports", "NotificationSeen")
    )
    return N, R


def _notification_sender_str(obj) -> str:
    f = _model_fields(obj.__class__)
    for cand in ("sender", "created_by", "author", "user", "teacher", "owner"):
        if cand in f:
            try:
                v = getattr(obj, cand, None)
                if v:
                    return str(
                        getattr(v, "name", None)
                        or getattr(v, "phone", None)
                        or getattr(v, "username", None)
                        or v
                    )
            except Exception:
                pass
    return "الإدارة"


def _exclude_notif_dismissed_cookies_notif_qs(qs, request: Optional[HttpRequest]):
    """استبعاد الإشعارات التي أخفاها المستخدم عبر الكوكي على مستوى Notification."""
    if not request:
        return qs
    try:
        ids = list(qs.values_list("id", flat=True)[:80])
        skip = [i for i in ids if request.COOKIES.get(f"notif_dismissed_{i}")]
        return qs.exclude(id__in=skip) if skip else qs
    except Exception:
        return qs


def _exclude_notif_dismissed_cookies_recipient_qs(qs, request: Optional[HttpRequest], notif_fk: str):
    """استبعاد سجلات الاستلام التي أخفاها المستخدم عبر الكوكي (يفترض وجود FK اسمه notif_fk)."""
    if not request:
        return qs
    try:
        ids = list(qs.values_list(f"{notif_fk}_id", flat=True)[:80])
        skip = [i for i in ids if request.COOKIES.get(f"notif_dismissed_{i}")]
        return qs.exclude(**{f"{notif_fk}_id__in": skip}) if skip else qs
    except Exception:
        return qs


def _published_notifications_qs(N):
    """فلترة نشر/نشاط/فترات زمنية على مستوى Notification."""
    qs = N.objects.all()
    now = timezone.now()
    f = _model_fields(N)
    try:
        if "is_active" in f:
            qs = qs.filter(is_active=True)
        if "status" in f and hasattr(N, "Status"):
            try:
                published_value = getattr(N.Status, "PUBLISHED", None)
                if published_value is not None:
                    qs = qs.filter(status=published_value)
            except Exception:
                pass
        # حقول أوقات شائعة
        if "starts_at" in f:
            qs = qs.filter(Q(starts_at__lte=now) | Q(starts_at__isnull=True))
        if "ends_at" in f:
            qs = qs.filter(Q(ends_at__gte=now) | Q(ends_at__isnull=True))
        if "publish_at" in f:
            qs = qs.filter(Q(publish_at__lte=now) | Q(publish_at__isnull=True))
        if "expires_at" in f:
            qs = qs.filter(Q(expires_at__gte=now) | Q(expires_at__isnull=True))
    except Exception:
        pass
    return qs


def _targeted_for_user_q(N, user) -> Q:
    """
    استهداف المستخدم مباشرة من موديل Notification (Fallback فقط).
    مشروعك يعتمد NotificationRecipient لذا هذا المسار يُستخدم فقط إذا لم يتوفر R.
    """
    f = _model_fields(N)
    q = Q()
    if "teacher" in f:
        q |= Q(teacher=user)
    if "user" in f:
        q |= Q(user=user)
    for m2m_name in ("recipients", "teachers", "users", "audience_teachers"):
        if m2m_name in f:
            try:
                q |= Q(**{f"{m2m_name}": user})
            except Exception:
                pass
    user_codes = _user_department_codes(user)
    if user_codes:
        if "department" in f:
            q |= Q(department__slug__in=user_codes) | Q(department__code__in=user_codes)
        if "departments" in f:
            q |= Q(departments__slug__in=user_codes) | Q(departments__code__in=user_codes)
    if "is_broadcast" in f:
        q |= Q(is_broadcast=True)
    return q


def _order_newest(qs, N_or_R):
    f = _model_fields(N_or_R)
    order_fields = []
    for cand in ("created_at", "created_on", "publish_at", "starts_at", "id"):
        if cand in f:
            order_fields.append(f"-{cand}")
    if order_fields:
        try:
            return qs.order_by(*order_fields)
        except Exception:
            pass
    return qs


def _notification_title_body_dict(obj) -> Tuple[str, str]:
    f = _model_fields(obj.__class__)
    title = ""
    for cand in ("title", "subject", "heading", "name"):
        if cand in f:
            try:
                title = getattr(obj, cand) or ""
                break
            except Exception:
                pass
    body = ""
    for cand in ("body", "message", "content", "text", "details"):
        if cand in f:
            try:
                body = getattr(obj, cand) or ""
                break
            except Exception:
                pass
    return (str(title).strip() or "إشعار"), str(body or "")


def _build_hero_payload_from_notification(n) -> Dict[str, Any]:
    title, body = _notification_title_body_dict(n)
    data: Dict[str, Any] = {
        "id": getattr(n, "pk", None),
        "title": title,
        "body": body,
        "sender_name": _notification_sender_str(n),
    }
    f = _model_fields(n.__class__)
    for cand in ("action_url", "url", "link"):
        if cand in f:
            try:
                data["action_url"] = getattr(n, cand) or ""
                break
            except Exception:
                pass
    return data


def _pick_hero_notification(user, request: Optional[HttpRequest] = None) -> Optional[Dict[str, Any]]:
    """
    يُعيد حمولة نافذة هيرو المنبثقة:
    - أولاً عبر NotificationRecipient (غير مقروء → أحدث)،
    - وإلا فالباك عبر Notification موجه للمستخدم (إن وُجد).
    """
    N, R = _notification_models()
    if not N:
        return None

    uid = int(getattr(user, "id", 0) or 0)
    sid = _active_school_id_from_request(request)
    ttl = _nav_cache_ttl_seconds()
    cache_key = f"nav:hero:v1:u{uid}:s{sid}"
    if uid and ttl > 0:
        try:
            cached_val = cache.get(cache_key)
            if cached_val is not None:
                return cached_val
        except Exception:
            pass

    # المسار المفضل: عبر سجلات الاستلام (Recipient)
    if R:
        fR = _model_fields(R)

        # اكتشاف أسماء الحقول
        notif_fk = None
        for cand in ("notification", "notif", "message"):
            if cand in fR:
                notif_fk = cand
                break
        user_fk = None
        for cand in ("teacher", "user", "recipient"):
            if cand in fR:
                user_fk = cand
                break

        if notif_fk and user_fk:
            try:
                now = timezone.now()
                qs = R.objects.select_related(notif_fk)

                # فلترة تخصّص المستلم
                qs = qs.filter(**{user_fk: user})

                # عزل حسب المدرسة النشطة (مع السماح بإشعارات عامة school=NULL)
                try:
                    if request is not None:
                        sid = request.session.get("active_school_id")
                    else:
                        sid = None
                    fN = _model_fields(N)
                    if sid and "school" in fN:
                        qs = qs.filter(
                            Q(**{f"{notif_fk}__school_id": sid}) |
                            Q(**{f"{notif_fk}__school_id__isnull": True})
                        )
                except Exception:
                    pass

                # غير مقروء
                if "is_read" in fR:
                    qs = qs.filter(is_read=False)
                elif "read_at" in fR:
                    qs = qs.filter(Q(read_at__isnull=True))

                # استبعاد المنتهي/غير المنشور عبر FK إلى Notification
                fN = _model_fields(N)
                if "expires_at" in fN:
                    qs = qs.filter(**{f"{notif_fk}__expires_at__gt": now}) | qs.filter(
                        **{f"{notif_fk}__expires_at__isnull": True}
                    )
                if "is_active" in fN:
                    qs = qs.filter(**{f"{notif_fk}__is_active": True})
                if "publish_at" in fN:
                    qs = qs.filter(**{f"{notif_fk}__publish_at__lte": now}) | qs.filter(
                        **{f"{notif_fk}__publish_at__isnull": True}
                    )
                if "starts_at" in fN:
                    qs = qs.filter(**{f"{notif_fk}__starts_at__lte": now}) | qs.filter(
                        **{f"{notif_fk}__starts_at__isnull": True}
                    )
                if "ends_at" in fN:
                    qs = qs.filter(**{f"{notif_fk}__ends_at__gte": now}) | qs.filter(
                        **{f"{notif_fk}__ends_at__isnull": True}
                    )

                # استبعاد الكوكي (Dismiss)
                qs = _exclude_notif_dismissed_cookies_recipient_qs(qs, request, notif_fk)

                # ترتيب بالأحدث الممكن
                qs = _order_newest(qs, R)

                rec = qs.first()
                if rec:
                    try:
                        n = getattr(rec, notif_fk)
                    except Exception:
                        n = None
                    if n:
                        payload = _build_hero_payload_from_notification(n)
                        if uid and ttl > 0:
                            try:
                                cache.set(cache_key, payload, ttl)
                            except Exception:
                                pass
                        return payload
            except Exception:
                pass

    # فالباك: مباشرة من Notification (يعمل فقط إن كان هناك استهداف عبر حقول الـ Notification نفسها)
    try:
        now = timezone.now()
        base_qs = _published_notifications_qs(N).filter(_targeted_for_user_q(N, user)).distinct()

        # عزل حسب المدرسة النشطة (مع السماح بإشعارات عامة school=NULL)
        try:
            sid = request.session.get("active_school_id") if request is not None else None
            fN = _model_fields(N)
            if sid and "school" in fN:
                base_qs = base_qs.filter(Q(school_id=sid) | Q(school__isnull=True))
        except Exception:
            pass
        base_qs = _exclude_notif_dismissed_cookies_notif_qs(base_qs, request)
        base_qs = _order_newest(base_qs, N)

        obj = base_qs.only("id")[:1].first()
        if obj:
            payload = _build_hero_payload_from_notification(obj)
            if uid and ttl > 0:
                try:
                    cache.set(cache_key, payload, ttl)
                except Exception:
                    pass
            return payload

        # فرصة ثانية: نطاق آخر 3 أيام
        try:
            fN = _model_fields(N)
            recent_qs = _published_notifications_qs(N).filter(_targeted_for_user_q(N, user)).distinct()
            three_days_ago = now - timedelta(days=3)
            if "created_at" in fN:
                recent_qs = recent_qs.filter(created_at__gte=three_days_ago)
            elif "created_on" in fN:
                recent_qs = recent_qs.filter(created_on__gte=three_days_ago)
            elif "publish_at" in fN:
                recent_qs = recent_qs.filter(publish_at__gte=three_days_ago)
            recent_qs = _exclude_notif_dismissed_cookies_notif_qs(recent_qs, request)
            recent_qs = _order_newest(recent_qs, N)
            obj = recent_qs.only("id")[:1].first()
            if obj:
                payload = _build_hero_payload_from_notification(obj)
                if uid and ttl > 0:
                    try:
                        cache.set(cache_key, payload, ttl)
                    except Exception:
                        pass
                return payload
        except Exception:
            pass
    except Exception:
        pass

    if uid and ttl > 0:
        try:
            cache.set(cache_key, None, ttl)
        except Exception:
            pass

    return None


def _unread_count(user, request: Optional[HttpRequest] = None) -> int:
    """عدد الإشعارات غير المقروءة للمستخدم."""
    N, R = _notification_models()
    if not N:
        return 0

    uid = int(getattr(user, "id", 0) or 0)
    sid = _active_school_id_from_request(request)
    ttl = _nav_cache_ttl_seconds()
    cache_key = f"nav:unread:v2:u{uid}:s{sid}"
    if uid and ttl > 0:
        try:
            cached_val = cache.get(cache_key)
            if cached_val is not None:
                return int(cached_val)
        except Exception:
            pass

    # المسار المفضل: NotificationRecipient
    if R:
        try:
            fR = _model_fields(R)
            user_fk = None
            for cand in ("teacher", "user", "recipient"):
                if cand in fR:
                    user_fk = cand
                    break
            if not user_fk:
                return 0

            notif_fk = None
            for cand in ("notification", "notif", "message"):
                if cand in fR:
                    notif_fk = cand
                    break

            qs = R.objects.filter(**{user_fk: user})

            # عزل حسب المدرسة النشطة (مع السماح بإشعارات عامة school=NULL)
            try:
                sid = request.session.get("active_school_id") if request is not None else None
                fN = _model_fields(N)
                if sid and notif_fk and "school" in fN:
                    qs = qs.filter(
                        Q(**{f"{notif_fk}__school_id": sid}) |
                        Q(**{f"{notif_fk}__school_id__isnull": True})
                    )
            except Exception:
                pass

            if "is_read" in fR:
                qs = qs.filter(is_read=False)
            elif "read_at" in fR:
                qs = qs.filter(read_at__isnull=True)

            # استبعاد المنتهي عبر FK إن أمكن
            if notif_fk:
                fN = _model_fields(N)
                now = timezone.now()

                # فصل: احتساب غير المقروء للإشعارات فقط (يستبعد التعاميم)
                try:
                    if "requires_signature" in fN:
                        qs = qs.filter(**{f"{notif_fk}__requires_signature": False})
                except Exception:
                    pass

                if "expires_at" in fN:
                    qs = qs.filter(**{f"{notif_fk}__expires_at__gt": now}) | qs.filter(
                        **{f"{notif_fk}__expires_at__isnull": True}
                    )
                if "is_active" in fN:
                    qs = qs.filter(**{f"{notif_fk}__is_active": True})

            val = _safe_count(qs)
            if uid and ttl > 0:
                try:
                    cache.set(cache_key, int(val), ttl)
                except Exception:
                    pass
            return val
        except Exception:
            return 0

    # فالباك: بلا سجل استلام → نعجز عن قياس غير المقروء بدقة
    try:
        qs = _published_notifications_qs(N).filter(_targeted_for_user_q(N, user)).distinct()

        # عزل حسب المدرسة النشطة (مع السماح بإشعارات عامة school=NULL)
        try:
            sid = request.session.get("active_school_id") if request is not None else None
            fN = _model_fields(N)
            if sid and "school" in fN:
                qs = qs.filter(Q(school_id=sid) | Q(school__isnull=True))
        except Exception:
            pass
        val = _safe_count(qs)
        if uid and ttl > 0:
            try:
                cache.set(cache_key, int(val), ttl)
            except Exception:
                pass
        return val
    except Exception:
        return 0


def _reverse_any(names: Iterable[str]) -> Optional[str]:
    for n in names:
        try:
            return reverse(n)
        except Exception:
            continue
    return None


def _school_role_labels(active_school: Optional[School]) -> Dict[str, str]:
    """Compatibility wrapper around the canonical gender-aware labels."""
    labels = school_gender_labels(active_school)
    return {
        "manager": str(labels["manager"]),
        "teacher": str(labels["teacher"]),
        "teachers": str(labels["teachers"]),
        "teachers_obj": str(labels["teachers_object"]),
        "head": str(labels["head"]),
        "head_of_department": str(labels["head_of_department"]),
        "admin_staff": str(labels["admin_staff"]),
        "lab_tech": str(labels["lab_tech"]),
    }


def _pending_signatures_count(user, request: Optional[HttpRequest] = None) -> int:
    """عدد التعاميم التي تتطلب توقيع ولم يتم توقيعها بعد للمستخدم."""
    N, R = _notification_models()
    if N is None or R is None:
        return 0

    fR = _model_fields(R)
    if "is_signed" not in fR:
        return 0

    # تحديد اسم FK للإشعار داخل سجل الاستلام
    notif_fk = None
    for cand in ("notification", "notif", "announcement", "message"):
        if cand in fR:
            notif_fk = cand
            break
    if not notif_fk:
        return 0

    # تحديد اسم المستخدم داخل السجل
    user_fk = None
    for cand in ("teacher", "user"):
        if cand in fR:
            user_fk = cand
            break
    if not user_fk:
        return 0

    uid = int(getattr(user, "id", 0) or 0)
    sid = _active_school_id_from_request(request)
    ttl = _nav_cache_ttl_seconds()
    cache_key = f"nav:pending-sign:v1:u{uid}:s{sid}"
    if uid and ttl > 0:
        try:
            cached_val = cache.get(cache_key)
            if cached_val is not None:
                return int(cached_val)
        except Exception:
            pass

    now = timezone.now()
    try:
        qs = R.objects.filter(**{user_fk: user, "is_signed": False, f"{notif_fk}__requires_signature": True})

        # فلترة نشر/انتهاء على مستوى Notification إن وُجدت
        fN = _model_fields(N)
        try:
            if "is_active" in fN:
                qs = qs.filter(**{f"{notif_fk}__is_active": True})
        except Exception:
            pass
        try:
            if "expires_at" in fN:
                qs = qs.filter(
                    Q(**{f"{notif_fk}__expires_at__gte": now}) | Q(**{f"{notif_fk}__expires_at__isnull": True})
                )
        except Exception:
            pass

        # عزل حسب المدرسة النشطة (مع السماح بإشعارات عامة school=NULL)
        try:
            sid = request.session.get("active_school_id") if request is not None else None
            if sid and "school" in fN:
                qs = qs.filter(Q(**{f"{notif_fk}__school_id": sid}) | Q(**{f"{notif_fk}__school__isnull": True}))
        except Exception:
            pass

        qs = _exclude_notif_dismissed_cookies_recipient_qs(qs, request, notif_fk=notif_fk)
        val = _safe_count(qs)
        if uid and ttl > 0:
            try:
                cache.set(cache_key, int(val), ttl)
            except Exception:
                pass
        return val
    except Exception:
        return 0


# -----------------------------
# أعلام الصلاحيات المُنطَقة
# -----------------------------
# **علمٌ لكل صلاحية تفتح باباً في القائمة.** كانت القائمة تعرف شرطاً واحداً هو
# «مدير مدرسة»، فكل ما يُمنح للوكيل والموظف الإداري يعمل في العرض ولا يجد
# رابطاً: الصلاحية نافذة، والشاشة قائمة، والطريق إليها كتابةُ المسار يدوياً.
#
# والاتجاه المعاكس محكومٌ بالأعلام نفسها: رابطٌ يظهر لمن لا يملك صلاحيته يردّه
# العرضُ برسالة منع — وفعلٌ مرئي ممنوع أسوأ ما يقابله مستخدم.
#
# **الحساب مرة واحدة لكل طلب.** ``scope_capabilities`` و``delegated_capabilities``
# تُخزَّنان على كائن المستخدم، فبناء كل الأعلام من اتحادهما استعلامان لا استعلام
# لكل علم — والبديل (نداء ``capability_source`` لكل رمز) يضاعف الكلفة بلا فائدة.
# الرموز تُقرأ من مرجع الصلاحيات لا تُكتب نصاً: رمزٌ مكتوب بيده هنا يصمت عند
# أي تغيير في المرجع، فيختفي رابطٌ بلا خطأ واحد.
_NAV_CAPABILITY_FLAGS: tuple[tuple[str, str], ...] = (
    ("CAN_VIEW_SCHOOL_DASHBOARD", caps.VIEW_SCHOOL_DASHBOARD),
    ("CAN_REVIEW_APPROVALS", caps.REVIEW_REPORTS),
    ("CAN_RECOMMEND_APPROVAL", caps.RECOMMEND_APPROVAL),
    ("CAN_VIEW_ACHIEVEMENTS", caps.VIEW_ACHIEVEMENTS),
    ("CAN_HANDLE_REQUESTS", caps.HANDLE_REQUESTS),
    ("CAN_DRAFT_CIRCULARS", caps.DRAFT_CIRCULARS),
    ("CAN_VIEW_SCHOOL_AUDIT", caps.VIEW_AUDIT_LOG),
    ("CAN_ASSIGN_TASKS", caps.ASSIGN_TASKS),
    ("CAN_MANAGE_MEETINGS", caps.MANAGE_MEETINGS),
    ("CAN_TRACK_PLANS", caps.TRACK_PLANS),
    ("CAN_ARCHIVE_DOCUMENTS", caps.ARCHIVE_DOCUMENTS),
    ("CAN_MANAGE_LAB", caps.MANAGE_LAB),
)


def _empty_capability_flags() -> Dict[str, bool]:
    return {name: False for name, _code in _NAV_CAPABILITY_FLAGS}


def _capability_flags(
    user,
    active_school: Optional[School],
    *,
    is_school_manager: bool,
) -> Dict[str, bool]:
    """علمٌ لكل صلاحية، للمستخدم في مدرسته النشطة.

    مدير المدرسة ومالك النظام يمرّان بكل الأعلام: الأول يملك كل شيء في مدرسته
    بحكم دوره، والثاني يمر دائماً — وهي القاعدة نفسها التي تنفّذها
    ``has_capability``، مكتوبةً هنا مرة واحدة لا في أحد عشر شرطاً.
    """
    flags = _empty_capability_flags()
    if not getattr(user, "is_authenticated", False):
        return flags

    if getattr(user, "is_superuser", False) or is_school_manager:
        # المدير بلا مدرسة نشطة لا يُفتح له شيء: كل هذه الشاشات تسأل عن مدرسة
        # أولاً، ورابطٌ يقود إلى «فضلاً اختر مدرسة» ليس رابطاً.
        allow_all = bool(active_school is not None or getattr(user, "is_superuser", False))
        return {name: allow_all for name, _code in _NAV_CAPABILITY_FLAGS}

    if active_school is None:
        return flags

    try:
        from .permissions import delegated_capabilities, scope_capabilities

        granted = set(scope_capabilities(user, active_school)) | set(
            delegated_capabilities(user, active_school)
        )
    except Exception:
        return flags

    for name, code in _NAV_CAPABILITY_FLAGS:
        flags[name] = code in granted
    return flags


# ما يُجمع في مجموعة «الإشراف» للوكيل والموظف الإداري. الشريط المسطّح لا يحتمل
# ثمانية مداخل جديدة — وهي العلّة نفسها التي جُمّع من أجلها شريط المدير حين
# امتلأ حتى ركب على اسم المدرسة.
_SUPERVISION_FLAGS: tuple[str, ...] = (
    "CAN_VIEW_SCHOOL_DASHBOARD",
    "CAN_ASSIGN_TASKS",
    "CAN_MANAGE_MEETINGS",
    "CAN_TRACK_PLANS",
    "CAN_VIEW_ACHIEVEMENTS",
    "CAN_HANDLE_REQUESTS",
    "CAN_DRAFT_CIRCULARS",
    "CAN_ARCHIVE_DOCUMENTS",
    "CAN_MANAGE_LAB",
)


def _shows_supervision_group(flags: Dict[str, bool], *, is_school_manager: bool) -> bool:
    """هل يستحق هذا المستخدم مجموعة «الإشراف» في شريطه؟

    المدير خارجها: مجموعاته الخمس تغطّي هذه الوجهات كلها، وإضافتها له تكرار.
    ومن لا يملك منها شيئاً خارجها كذلك — فمجموعةٌ فارغة زرٌّ يفتح لا شيء.
    """
    if is_school_manager:
        return False
    return any(bool(flags.get(name)) for name in _SUPERVISION_FLAGS)


# -----------------------------
# المُعالج الرئيس لكونتكست التنقل
# -----------------------------
# ... (باقي الاستيرادات والدوال كما لديك تمامًا)

def nav_context(request: HttpRequest) -> Dict[str, Any]:
    u = getattr(request, "user", None)
    if not u or not getattr(u, "is_authenticated", False):
        return {
            "NAV_MY_OPEN_TICKETS": 0,
            "NAV_ASSIGNED_TO_ME": 0,
            "IS_OFFICER": False,
            "OFFICER_DEPARTMENT": None,
            "OFFICER_DEPARTMENTS": [],
            "SHOW_OFFICER_REPORTS_LINK": False,
            "SHOW_DEPARTMENT_REPORTS_LINK": False,
            "SHOW_SCHOOL_REPORTS_LINK": False,
            "SHOW_ARCHIVE_LINK": False,
            "IS_SCHOOL_MANAGER": False,
            "IS_SCHOOL_DEPUTY": False,
            "IS_ADMIN_STAFF": False,
            "IS_LAB_TECHNICIAN": False,
            "SHOW_LAB_NAV": False,
            "SHOW_ASSIGNED_TO_ME": False,
            "SHOW_SUPERVISION_GROUP": False,
            "IS_EXECUTIVE_DIRECTOR": False,
            "GROUP_NAME": None,
            **_empty_capability_flags(),
            "DEPARTMENT_REPORTS_URLNAME": None,
            "NAV_OFFICER_REPORTS": 0,
            "SHOW_ADMIN_DASHBOARD_LINK": False,
            "NAV_NOTIFICATIONS_UNREAD": 0,
            "NAV_SIGNATURES_PENDING": 0,
            "NAV_NOTIFICATION_HERO": None,
            "CAN_SEND_NOTIFICATIONS": False,
            "SEND_NOTIFICATION_URL": None,
            "SCHOOL_NAME": None,
            "SCHOOL_LOGO_URL": None,
            "USER_ROLE_LABEL": None,
            **school_gender_template_context(None),
        }

    # -----------------------------
    # Short-TTL caching (per user + active_school + dismissal cookies)
    # - Cuts repeated COUNT/exists queries across page navigations.
    # - Cookie signature ensures dismissing a hero/notification invalidates cache quickly.
    # -----------------------------
    try:
        ttl = int(getattr(settings, "NAV_CONTEXT_CACHE_TTL_SECONDS", 20) or 0)
    except Exception:
        ttl = 20

    cache_key = None
    if ttl > 0:
        try:
            sid_raw = request.session.get("active_school_id")
            sid_for_key = str(int(sid_raw)) if sid_raw else "none"
        except Exception:
            sid_for_key = "none"

        try:
            dismissed_keys = [k for k in (request.COOKIES or {}).keys() if k.startswith("notif_dismissed_")]
            dismissed_keys.sort()
            dismissed_sig = hashlib.sha256("|".join(dismissed_keys).encode("utf-8")).hexdigest()[:12]
        except Exception:
            dismissed_sig = "nocookies"

        try:
            uid = int(getattr(u, "id", 0) or 0)
            role_version = int(cache.get(f"navctx:role-version:u{uid}", 1) or 1)
            user_flags = "".join(
                [
                    "s" if getattr(u, "is_superuser", False) else "-",
                    "f" if getattr(u, "is_staff", False) else "-",
                ]
            )
            role_id = int(getattr(u, "role_id", 0) or 0)
            cache_key = (
                # v5: صار الكونتكست يحمل أعلام الصلاحيات وأدوار المدرسة. ورقم
                # الإصدار يُزاد مع كل تغيير في **شكل** الناتج لا في قيمته: قيمةٌ
                # مخزَّنة بالشكل القديم تصل قالباً يقرأ مفاتيح لا وجود لها فيها،
                # فتُقرأ فارغةً بلا خطأ — وتختفي روابط لثوانٍ بعد كل نشر.
                f"navctx:v6:u{uid}:s{sid_for_key}:r{role_id}:"
                f"f{user_flags}:v{role_version}:c{dismissed_sig}"
            )
            cached = cache.get(cache_key)
            if isinstance(cached, dict):
                return cached
        except Exception:
            cache_key = None

    # نحدد المدرسة النشطة (إن وُجدت) لاستخدامها في العدادات
    # ── أولاً نحاول إعادة استخدام ما حمّله الـ middleware ──
    active_school = getattr(request, "active_school", None)
    if active_school is None:
        try:
            sid = request.session.get("active_school_id")
            if sid:
                active_school = School.objects.filter(pk=sid, is_active=True).first()
        except Exception:
            active_school = None

    gender_context = school_gender_template_context(active_school)

    try:
        ticket_base = Ticket.objects.filter(status__in=UNRESOLVED_STATES)
        if active_school is not None:
            ticket_base = ticket_base.filter(school=active_school)
        ticket_agg = ticket_base.aggregate(
            my_open=Count("id", filter=Q(creator=u)),
            assigned_open=Count("id", filter=Q(assignee=u)),
        )
        my_open = ticket_agg["my_open"]
        assigned_open = ticket_agg["assigned_open"]
    except Exception:
        my_open = 0
        assigned_open = 0

    # تحديد عضويات المدرسة/القسم في استعلامات مجمعة لتقليل كلفة كل طلب.
    manager_school_ids: set[int] = set()
    officer_depts: list[Department] = []
    member_depts: list[Department] = []

    any_school_manager = False
    is_school_manager = False
    try:
        if getattr(u, "is_authenticated", False):
            manager_school_ids = get_school_manager_school_ids(u)
            any_school_manager = bool(manager_school_ids)
            if active_school is not None:
                is_school_manager = int(getattr(active_school, "id", 0) or 0) in manager_school_ids
            else:
                is_school_manager = any_school_manager
    except Exception:
        manager_school_ids = set()

    try:
        Membership = _get_membership_model()
        if Membership is not None:
            membs = Membership.objects.select_related("department").filter(
                teacher=u,
                department__is_active=True,
            )
            if active_school is not None and "school" in _model_fields(Department):
                membs = membs.filter(department__school=active_school)

            officer_values = set(_officer_role_values(Membership))
            teacher_values = set(_teacher_role_values(Membership))
            seen_officer: set[int] = set()
            seen_member: set[int] = set()
            for m in membs:
                d = getattr(m, "department", None)
                if d is None or getattr(d, "pk", None) is None:
                    continue
                role_type = getattr(m, "role_type", None)
                did = int(d.pk)
                if role_type in officer_values and did not in seen_officer:
                    seen_officer.add(did)
                    officer_depts.append(d)
                if role_type in teacher_values and did not in seen_member:
                    seen_member.add(did)
                    member_depts.append(d)
    except Exception:
        officer_depts = []
        member_depts = []

    is_officer = bool(officer_depts)
    show_officer_link = bool(getattr(u, "is_superuser", False) or is_officer)
    is_member = bool(member_depts)
    
    # مدير المدرسة لا يظهر له "تقارير قسمي" بل "تقارير المدرسة"
    show_dept_reports_link = bool((show_officer_link or is_member) and not is_school_manager)
    dept_reports_urlname = "reports:officer_reports" if show_officer_link else "reports:department_reports"
    
    # رابط تقارير المدرسة لمدير المدرسة فقط
    show_school_reports_link = bool(is_school_manager and active_school is not None)

    # تقارير officer
    nav_officer_reports = 0
    try:
        start_date = timezone.localdate() - timedelta(days=7)
        base_qs = Report.objects.filter(report_date__gte=start_date)
        if active_school is not None and "school" in {f.name for f in Report._meta.get_fields()}:
            base_qs = base_qs.filter(school=active_school)

        if getattr(u, "is_superuser", False):
            nav_officer_reports = base_qs.count()
        elif is_officer:
            dept_ids = [int(getattr(d, "pk", 0) or 0) for d in officer_depts if getattr(d, "pk", None)]
            if dept_ids:
                try:
                    rt_ids = {
                        int(x)
                        for x in Department.objects.filter(pk__in=dept_ids).values_list("reporttypes__id", flat=True)
                        if x
                    }
                except Exception:
                    rt_ids = set()

                if rt_ids:
                    nav_officer_reports = base_qs.filter(category_id__in=list(rt_ids)).count()
    except Exception:
        nav_officer_reports = 0

    has_saved_archive = False
    if active_school is not None:
        try:
            has_saved_archive = SchoolYearArchive.objects.filter(
                school=active_school,
                status__in=[
                    SchoolYearArchive.Status.READY,
                    SchoolYearArchive.Status.PARTIAL,
                ],
            ).exists()
        except Exception:
            has_saved_archive = False
    archive_addon_active = bool(
        active_school is not None
        and school_has_archive_addon(active_school)
    )
    show_archive_link = bool(
        active_school is not None
        and (
            is_school_manager
            or archive_addon_active
            or has_saved_archive
        )
    )

    # روابط لوحة المدير: تظهر لكل من لديه is_staff (مدير/سوبر أدمن) أو مدير مدرسة
    show_admin_link = bool(getattr(u, "is_staff", False)) or any_school_manager

    # المدير التنفيذي لمجموعة المدارس المتكاملة — رابط لوحة المجموعة وحده،
    # فالدور إشرافي لا يفتح أي مسار تحرير.
    group_name = None
    try:
        from .permissions import executive_director_groups as _director_groups
        from .permissions import is_executive_director as _is_executive_director

        is_executive_director_user = bool(_is_executive_director(u))
        if is_executive_director_user:
            # اسم المجموعة يحلّ محل اسم المدرسة في الترويسة: المدير التنفيذي
            # لا مدرسةَ نشطةَ له، فكانت ترويسته تقول «منصة توثيق» — اسمُ
            # المنتَج لا اسمُ ما يقوده.
            first_group = _director_groups(u).first()
            group_name = getattr(first_group, "name", None)
    except Exception:
        is_executive_director_user = False
        group_name = None

    # الأدوار المدرسية — القائمة كانت تعرف «مديراً» أو «معلّماً» ولا شيء بينهما.
    try:
        from .permissions import can_view_lab as _can_view_lab
        from .permissions import is_admin_staff as _is_admin_staff
        from .permissions import is_lab_technician as _is_lab_technician
        from .permissions import is_school_deputy as _is_school_deputy

        is_deputy_user = bool(
            active_school is not None and _is_school_deputy(u, active_school)
        )
        is_admin_staff_user = bool(
            active_school is not None and _is_admin_staff(u, active_school)
        )
        # المحضّر يُحسم بمسمّاه الوظيفي: المختبر عملُه لا صلاحيةٌ تُمنح له.
        is_lab_tech_user = bool(
            active_school is not None and _is_lab_technician(u, active_school)
        )
        show_lab_nav = bool(active_school is not None and _can_view_lab(u, active_school))
    except Exception:
        is_deputy_user = False
        is_admin_staff_user = False
        is_lab_tech_user = False
        show_lab_nav = False

    # أعلام الصلاحيات المُنطَقة — مصدر شروط القائمة كلها.
    capability_flags = _capability_flags(
        u, active_school, is_school_manager=is_school_manager
    )
    show_supervision_group = _shows_supervision_group(
        capability_flags, is_school_manager=is_school_manager
    )

    # تسمية دور المستخدم الحالي (لعرضها في الواجهة)
    user_role_label: Optional[str] = effective_user_role_label(u, active_school=active_school)

    # من يحق له إرسال إشعارات؟
    # الإرسال يجب أن يكون ضمن مدرسة محددة لغير السوبر
    can_send_notifications = bool(getattr(u, "is_superuser", False) or (active_school is not None and (is_officer or is_school_manager)))

    # اختر الرابط الأنسب الذي يملك إذن الدخول إليه
    send_notification_url = None
    if can_send_notifications:
        # نفضّل المسار المحمي الموحّد
        send_notification_url = _reverse_any([
            "reports:notifications_create",   # يسمح للمدير/المسؤول (بعد تعديل الديكوريتر)
            "reports:send_notification",      # fallback قديم
            "reports:notification_create",
            "reports:announcement_create",
            "reports:admin_message_create",
            "reports:notifications_send",
        ])

    # عداد الإشعارات + الـ Hero
    try:
        unread_count = _unread_count(u, request=request)
    except Exception:
        unread_count = 0

    try:
        signatures_pending = _pending_signatures_count(u, request=request)
    except Exception:
        signatures_pending = 0
    try:
        hero = _pick_hero_notification(u, request=request)
    except Exception:
        hero = None

    # المدرسة النشطة + قائمة مدارس المستخدم (للتبديل في الهيدر)
    school_name = None
    school_logo = None
    school_id = None
    user_schools: list[School] = []
    try:
        if getattr(request.user, "is_authenticated", False):
            if any_school_manager:
                # للمدير: أظهر فقط المدارس التي يملك فيها دور مدير مدرسة.
                user_schools = list(
                    School.objects.filter(
                        id__in=list(manager_school_ids),
                        is_active=True,
                    )
                    .order_by("name")
                )
            else:
                # لباقي المستخدمين: المدارس المرتبطة بعضوياتهم النشطة.
                user_schools = list(
                    School.objects.filter(
                        memberships__teacher=request.user,
                        memberships__is_active=True,
                        is_active=True,
                    )
                    .distinct()
                    .order_by("name")
                )
        # ── إعادة استخدام active_school المحمّل أعلاه بدل query جديد ──
        if active_school is not None:
            school_id = active_school.pk
            school_name = active_school.name
            # تم حذف شعارات المدارس (logo_file/logo_url) نهائيًا من النظام
            school_logo = None
    except Exception:
        school_name = None
        school_logo = None
        school_id = None
        user_schools = []

    out = {
        "NAV_MY_OPEN_TICKETS": my_open,
        "NAV_ASSIGNED_TO_ME": assigned_open,
        "IS_OFFICER": is_officer,
        "OFFICER_DEPARTMENT": officer_depts[0] if officer_depts else None,
        "OFFICER_DEPARTMENTS": officer_depts,
        "SHOW_OFFICER_REPORTS_LINK": show_officer_link,
        "SHOW_DEPARTMENT_REPORTS_LINK": show_dept_reports_link,
        "SHOW_SCHOOL_REPORTS_LINK": show_school_reports_link,
        "SHOW_ARCHIVE_LINK": show_archive_link,
        "ARCHIVE_ADDON_ACTIVE": archive_addon_active,
        "HAS_SAVED_ARCHIVE": has_saved_archive,
        "DEPARTMENT_REPORTS_URLNAME": dept_reports_urlname,
        "NAV_OFFICER_REPORTS": nav_officer_reports,
        "SHOW_ADMIN_DASHBOARD_LINK": show_admin_link,
        "IS_SCHOOL_MANAGER": is_school_manager,
        "IS_SCHOOL_DEPUTY": is_deputy_user,
        "IS_ADMIN_STAFF": is_admin_staff_user,
        "IS_LAB_TECHNICIAN": is_lab_tech_user,
        "SHOW_LAB_NAV": show_lab_nav,
        "IS_EXECUTIVE_DIRECTOR": is_executive_director_user,
        "GROUP_NAME": group_name,
        # «مهامي المعيّنة» كان مشروطاً بكوْن المستخدم مسؤول قسم، والعدّاد يُحسب
        # للجميع: طلبٌ يُحال إلى موظف إداري يُحتسب في القائمة ولا رابط يوصله
        # إليه. والشرط الصحيح هو وجود ما يُعرض لا حملُ دور بعينه.
        "SHOW_ASSIGNED_TO_ME": bool(
            is_officer or show_dept_reports_link or assigned_open
        ),
        "SHOW_SUPERVISION_GROUP": show_supervision_group,
        **capability_flags,
        "NAV_NOTIFICATIONS_UNREAD": unread_count,
        "NAV_SIGNATURES_PENDING": signatures_pending,
        "NAV_NOTIFICATION_HERO": hero,
        "CAN_SEND_NOTIFICATIONS": can_send_notifications,
        "SEND_NOTIFICATION_URL": send_notification_url,
        "SCHOOL_ID": school_id,
        "SCHOOL_NAME": school_name,
        "SCHOOL_LOGO_URL": school_logo,
        "USER_SCHOOLS": user_schools,
        "USER_ROLE_LABEL": user_role_label,
        **gender_context,
    }

    if cache_key and ttl > 0:
        try:
            cache.set(cache_key, out, ttl)
        except Exception:
            pass

    return out


def nav_counters(request: HttpRequest) -> Dict[str, int]:
    ctx = nav_context(request)
    return {
        "NAV_MY_OPEN_TICKETS": int(ctx.get("NAV_MY_OPEN_TICKETS", 0)),
        "NAV_ASSIGNED_TO_ME": int(ctx.get("NAV_ASSIGNED_TO_ME", 0)),
    }


def nav_badges(request: HttpRequest) -> Dict[str, Any]:
    return nav_context(request)


__all__ = ["nav_context", "nav_counters", "nav_badges"]


def csp(request: HttpRequest) -> Dict[str, Any]:
    """Expose CSP nonce to templates.

    The nonce is attached to the request by ContentSecurityPolicyMiddleware.
    """
    try:
        nonce = getattr(request, "csp_nonce", "") or ""
        if not nonce:
            # Fallback safety: ensure templates always receive a nonce value
            # even if middleware ordering or an upstream wrapper skipped setting it.
            import secrets

            nonce = secrets.token_urlsafe(16)
            request.csp_nonce = nonce
        return {"CSP_NONCE": nonce}
    except Exception:
        return {"CSP_NONCE": ""}


__all__.append("csp")


def seo(request: HttpRequest) -> Dict[str, Any]:
    """Expose canonical URLs and the public business identity."""
    site_url = str(getattr(settings, "SITE_URL", "") or "").strip().rstrip("/")
    if not site_url:
        site_url = request.build_absolute_uri("/").rstrip("/")
    business = {
        "legal_name": str(getattr(settings, "BUSINESS_LEGAL_NAME", "") or "").strip(),
        "commercial_registration": str(
            getattr(settings, "BUSINESS_COMMERCIAL_REGISTRATION", "") or ""
        ).strip(),
        "freelance_document_number": str(
            getattr(settings, "BUSINESS_FREELANCE_DOCUMENT_NUMBER", "") or ""
        ).strip(),
        "freelance_activity": str(
            getattr(settings, "BUSINESS_FREELANCE_ACTIVITY", "") or ""
        ).strip(),
        "freelance_document_expiry": str(
            getattr(settings, "BUSINESS_FREELANCE_DOCUMENT_EXPIRY", "") or ""
        ).strip(),
        "freelance_document_url": str(
            getattr(settings, "BUSINESS_FREELANCE_DOCUMENT_URL", "") or ""
        ).strip(),
        "tax_number": str(getattr(settings, "BUSINESS_TAX_NUMBER", "") or "").strip(),
        "licenses": str(getattr(settings, "BUSINESS_LICENSES", "") or "").strip(),
        "verification_url": str(
            getattr(settings, "BUSINESS_VERIFICATION_URL", "") or ""
        ).strip(),
        "address": str(getattr(settings, "BUSINESS_ADDRESS", "") or "").strip(),
        "support_email": str(
            getattr(settings, "BUSINESS_SUPPORT_EMAIL", "") or ""
        ).strip(),
        "support_phone": str(
            getattr(settings, "BUSINESS_SUPPORT_PHONE", "") or ""
        ).strip(),
    }
    business["disclosure_complete"] = bool(
        business["legal_name"]
        and (
            business["commercial_registration"]
            or business["freelance_document_number"]
        )
        and business["address"]
        and business["support_email"]
        and business["support_phone"]
    )
    return {"SITE_URL": site_url, "BUSINESS": business}


__all__.append("seo")


# -----------------------------
# بوابات الدفع المفعّلة
# -----------------------------
def payment_gateways(request: HttpRequest) -> Dict[str, Any]:
    """أعلام بوابات الدفع، متاحةً لكل قالب لا لصفحتين اثنتين.

    **لماذا معالج سياق لا متغيّر في كل عرض؟** لأن ذكر البوابة ليس شأن صفحة
    الدفع وحدها: سياسة الخصوصية تُفصح عن معالِج الدفع بوصفه معالجاً للبيانات،
    وسجلّ المدفوعات يعرض حالته، ولوحة المنصة كذلك. وكانت الأعلام تُمرَّر من
    عرضين فقط (صفحة الهبوط و«اشتراكي»)، فبقيت سياسة الخصوصية تسمّي «تمارا»
    اسماً صريحاً غير مشروط بشيء — وهي صفحة عامة يقرؤها كل زائر.

    والاسم التجاري لبوابةٍ لم تُعتمد بعد لا يجوز عرضه: العرض التزامٌ تجاري
    تجاه مزوّد الخدمة، لا تفصيلاً في الواجهة. فمصدرُ الحقيقة واحد، ومن أراد
    ذكر بوابة سأل عنه.
    """
    from .moyasar_gateway import is_enabled as _moyasar_enabled
    from .tamara_gateway import is_enabled as _tamara_enabled

    try:
        moyasar_on = bool(_moyasar_enabled())
    except Exception:
        moyasar_on = False
    try:
        tamara_on = bool(_tamara_enabled())
    except Exception:
        tamara_on = False

    names: List[str] = []
    if moyasar_on:
        names.append("ميسر")
    if tamara_on:
        names.append("تمارا")

    return {
        "moyasar_enabled": moyasar_on,
        "tamara_enabled": tamara_on,
        # جاهزة للعرض نصّاً: «ميسر» أو «ميسر وتمارا» أو فراغ عند تعطيلهما معاً.
        "active_payment_gateway_names": " و".join(names),
        "any_payment_gateway_enabled": bool(names),
    }


__all__.append("payment_gateways")
