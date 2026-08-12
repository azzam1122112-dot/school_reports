# reports/services_data_rights.py
# -*- coding: utf-8 -*-
"""حقوق صاحب البيانات: النسخة المقروءة، وطلب الإتلاف.

سياسة الخصوصية في المنصة تَعِد صراحةً بـ«الوصول، وطلب نسخة مقروءة… وطلب
الإتلاف في الحالات المقررة» عملاً بنظام حماية البيانات الشخصية السعودي. وكان
الوفاء بالوعد يمرّ بنموذج شكاوى ورسالة بريد ومعالجةٍ يدوية. هذه الوحدة تجعل
الأول فورياً، والثاني مسجَّلاً لا مُهمَلاً.

── ما يدخل النسخة ─────────────────────────────────────────────────────────
كل ما هو **عن صاحب الطلب**: ملفّه، وعضوياته، وما أنشأه أو كُلِّف به أو وصله.
والقاعدة في كل استعلام أن مفتاح المستخدم مثبَّتٌ في الشرط، لا مشتقٌّ من معطى
في الطلب — فليس في هذا المسار معامل يمكن التلاعب به للوصول إلى نسخة غيره.

── وما لا يدخلها، عمداً ───────────────────────────────────────────────────
**الأسرار ليست بيانات شخصية تُسلَّم.** ثلاثة أصناف مستثناة لأن تسليمها يخلق
الخطر الذي يُفترض أن يحمي منه هذا الحق:

* ``password`` و``current_session_key`` — تسليمهما تسليمُ الحساب نفسه.
* مادة WebAuthn (مُعرِّف الاعتماد والمفتاح العام) — تُسلَّم أسماء الأجهزة
  وتواريخها، لا ما يُصادَق به.
* عنوان اشتراك الدفع ومفاتيحه (``endpoint`` و``auth`` و``p256dh``) — من يملكها
  يستطيع دفعَ إشعارات إلى جهاز المستخدم. تُسلَّم حقيقةُ وجود اشتراك وتاريخه.

والملفات المرفوعة لا تُحزَم في النسخة: تُدرَج أسماؤها وأحجامها وروابطها داخل
المنصة. حزمُ عشرات الميغابايتات في طلب HTTP واحد يُسقط العامل، وللأرشفة
مسارُها المخصَّص الذي يعمل في الخلفية.

── التعليقات الخاصة: قرارٌ يستحق التصريح ──────────────────────────────────
``TeacherPrivateComment`` تعليقٌ يكتبه المدير **عن** المعلّم. وهو بيانات شخصية
عن صاحب الطلب بلا شك، لكنه في الوقت نفسه تقييمٌ إداري يخص طرفاً آخر ويحمل
رأيه. والنظام يُقيّد حق الوصول حين «يحمي حقوق شخص آخر» — وهو نصُّ سياسة
المنصة نفسها.

فالحل هنا وسط، وهو الوسط الصحيح: تُدرَج **حقيقةُ وجودها وعددُها وتواريخها**،
ولا يُدرَج نصُّها ولا كاتبُها. فصاحب البيانات يعلم أن عنه ملاحظات وكم هي ومتى
كُتبت — وهو جوهر «حق العلم» — ويبقى طلبُ النصّ مساراً يمرّ بمن يوازن الحقين.
"""
from __future__ import annotations

from typing import Any

from django.urls import reverse
from django.utils import timezone

from core.observability import soft_call


def _iso(value) -> str | None:
    if not value:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


def _file_reference(field) -> dict[str, Any] | None:
    """وصفُ ملفٍ لا محتواه: اسمُه وحجمُه وأين يُفتح."""
    name = getattr(field, "name", "") or ""
    if not name:
        return None
    return {
        "name": name.rsplit("/", 1)[-1],
        "path": name,
        "size_bytes": soft_call("data_rights.file_size", lambda: field.size, default=None),
    }


# ─────────────────────────────────────────────────────────────────────────
# أقسام النسخة
# ─────────────────────────────────────────────────────────────────────────
def _profile_section(user) -> dict[str, Any]:
    return {
        "name": user.name,
        "phone": user.phone,
        "email": user.email or None,
        "national_id": user.national_id or None,
        "date_joined": _iso(user.date_joined),
        "last_login": _iso(user.last_login),
        "is_active": bool(user.is_active),
    }


def _memberships_section(user) -> list[dict[str, Any]]:
    from .models import SchoolMembership

    rows = (
        SchoolMembership.objects.filter(teacher=user)
        .select_related("school")
        .order_by("school__name", "id")
    )
    return [
        {
            "school": getattr(row.school, "name", None),
            "role": row.get_role_type_display(),
            "job_title": row.job_title or None,
            "is_active": bool(row.is_active),
            "created_at": _iso(getattr(row, "created_at", None)),
        }
        for row in rows
    ]


def _reports_section(user) -> list[dict[str, Any]]:
    from .models import Report

    rows = (
        Report.objects.filter(teacher=user)
        .select_related("school", "category")
        .order_by("-report_date", "-id")
    )
    return [
        {
            "id": row.pk,
            "title": row.title,
            "school": getattr(row.school, "name", None),
            "category": getattr(row.category, "name", None),
            "report_date": _iso(row.report_date),
            "academic_year": row.academic_year or None,
            "idea": row.idea or None,
            "goal": getattr(row, "goal", None) or None,
            "results": getattr(row, "results", None) or None,
            "url": reverse("reports:report_print", args=[row.pk]),
        }
        for row in rows
    ]


def _tickets_section(user) -> list[dict[str, Any]]:
    from .models import Ticket, TicketNote

    tickets = (
        Ticket.objects.filter(creator=user)
        .select_related("school")
        .order_by("-created_at", "-id")
    )
    payload = [
        {
            "id": row.pk,
            "title": row.title,
            "body": row.body or None,
            "status": row.get_status_display(),
            "school": getattr(row.school, "name", None),
            "created_at": _iso(row.created_at),
        }
        for row in tickets
    ]
    notes = (
        TicketNote.objects.filter(author=user)
        .select_related("ticket")
        .order_by("-created_at", "-id")
    )
    return {
        "created": payload,
        "notes_written": [
            {
                "ticket_id": note.ticket_id,
                "body": note.body,
                "created_at": _iso(note.created_at),
            }
            for note in notes
        ],
    }


def _notifications_section(user) -> list[dict[str, Any]]:
    from .models import NotificationRecipient

    rows = (
        NotificationRecipient.objects.filter(teacher=user)
        .select_related("notification")
        .order_by("-created_at", "-id")
    )
    return [
        {
            "title": getattr(row.notification, "title", None),
            "is_circular": bool(getattr(row.notification, "requires_signature", False)),
            "received_at": _iso(row.created_at),
            "is_read": bool(row.is_read),
            "read_at": _iso(row.read_at),
            "is_signed": bool(getattr(row, "is_signed", False)),
            "signed_at": _iso(getattr(row, "signed_at", None)),
        }
        for row in rows
    ]


def _assignments_section(user) -> list[dict[str, Any]]:
    from .models import AssignmentTarget

    rows = (
        AssignmentTarget.objects.filter(assignee=user)
        .select_related("assignment")
        .order_by("-id")
    )
    return [
        {
            "assignment": getattr(row.assignment, "title", None),
            "due_at": _iso(getattr(row.assignment, "due_at", None)),
            "state": row.get_approval_state_display()
            if hasattr(row, "get_approval_state_display")
            else None,
        }
        for row in rows
    ]


def _achievements_section(user) -> list[dict[str, Any]]:
    from .models import TeacherAchievementFile

    rows = TeacherAchievementFile.objects.filter(teacher=user).order_by("-id")
    return [
        {
            "academic_year": row.academic_year,
            "pdf": _file_reference(getattr(row, "pdf_file", None)),
            "generated_at": _iso(getattr(row, "pdf_generated_at", None)),
        }
        for row in rows
    ]


def _documents_section(user) -> list[dict[str, Any]]:
    from .models import Document

    rows = Document.objects.filter(owner=user).order_by("-created_at", "-id")
    return [
        {
            "title": row.title,
            "description": row.description or None,
            "academic_year": row.academic_year or None,
            "created_at": _iso(row.created_at),
            "file": _file_reference(getattr(row, "file", None)),
        }
        for row in rows
    ]


def _activity_section(user, *, limit: int = 2000) -> list[dict[str, Any]]:
    from .models import AuditLog

    # ``AuditLog`` يسمّي عمود الوقت ``timestamp`` لا ``created_at``.
    rows = (
        AuditLog.objects.filter(teacher=user)
        .select_related("school")
        .order_by("-timestamp", "-id")[:limit]
    )
    return [
        {
            "action": row.get_action_display(),
            "model": row.model_name,
            "object": row.object_repr,
            "school": getattr(row.school, "name", None),
            "at": _iso(row.timestamp),
        }
        for row in rows
    ]


def _security_section(user) -> dict[str, Any]:
    """وجودُ وسائل الدخول وتواريخُها — لا موادُّها.

    راجع تعليل الاستثناء أعلى الملف: تسليم مادة الاعتماد أو مفاتيح الدفع
    يخلق الخطر الذي جاء حق الوصول ليحمي منه.
    """
    from .models import WebAuthnCredential, WebPushSubscription

    passkeys = WebAuthnCredential.objects.filter(teacher=user).order_by("-created_at")
    subscriptions = WebPushSubscription.objects.filter(teacher=user).order_by("-created_at")
    return {
        "passkeys": [
            {
                "device_name": row.device_name or None,
                "is_active": bool(row.is_active),
                "created_at": _iso(row.created_at),
                "last_used_at": _iso(row.last_used_at),
            }
            for row in passkeys
        ],
        "push_subscriptions": [
            {
                "created_at": _iso(row.created_at),
                "is_active": bool(getattr(row, "is_active", True)),
            }
            for row in subscriptions
        ],
    }


def _notes_about_me_section(user) -> dict[str, Any]:
    """حقيقةُ وجود ملاحظات إدارية عن صاحب الطلب — بلا نصّها.

    راجع التعليل أعلى الملف: النصّ رأيُ طرفٍ آخر، والنظام يُقيّد الوصول حين
    يحمي حقوق شخص آخر. والعلمُ بوجودها وعددها وتواريخها هو جوهر «حق العلم».
    """
    from .models import TeacherPrivateComment

    rows = TeacherPrivateComment.objects.filter(teacher=user).order_by("-created_at")
    return {
        "count": rows.count(),
        "dates": [_iso(row.created_at) for row in rows[:200]],
        "note": (
            "نصّ هذه الملاحظات لا يُسلَّم آلياً لأنها تحمل رأي طرف آخر. "
            "لطلب الاطلاع عليها، استخدم نموذج الشكاوى."
        ),
    }


SECTIONS = (
    ("profile", _profile_section),
    ("memberships", _memberships_section),
    ("reports", _reports_section),
    ("tickets", _tickets_section),
    ("notifications", _notifications_section),
    ("assignments", _assignments_section),
    ("achievement_files", _achievements_section),
    ("documents", _documents_section),
    ("notes_about_me", _notes_about_me_section),
    ("security", _security_section),
    ("activity_log", _activity_section),
)

# مفاتيح لا يجوز أن تظهر في النسخة مهما تغيّر الكود. يحرسها اختبار صريح.
FORBIDDEN_KEYS = frozenset(
    {
        "password",
        "current_session_key",
        "credential_id",
        "credential_id_hash",
        "public_key",
        "public_key_cose",
        "endpoint",
        "auth",
        "p256dh",
        "session_key",
        "secret",
        "token",
    }
)


def build_personal_data_export(user) -> dict[str, Any]:
    """النسخة الكاملة لصاحب الطلب.

    تعثّرُ قسمٍ لا يُسقط النسخة كلها: يُسجَّل باسمه ويُسلَّم الباقي مع بيانٍ
    بما نقص. ونسخةٌ ناقصة **مُعلَنة** أفضل من صفحة خطأ تترك صاحب الحق بلا شيء.
    """
    export: dict[str, Any] = {
        "generated_at": _iso(timezone.now()),
        "subject": user.name,
        "notice": (
            "هذه نسخة من بياناتك الشخصية في منصة توثيق، وفق نظام حماية البيانات "
            "الشخصية. لا تتضمّن كلمات المرور ولا مفاتيح المصادقة ولا مفاتيح "
            "الإشعارات — تسليمها يعرّض حسابك للخطر."
        ),
        "sections": {},
        "incomplete_sections": [],
    }

    for name, builder in SECTIONS:
        sentinel = object()
        value = soft_call(
            f"data_rights.section.{name}",
            lambda b=builder: b(user),
            default=sentinel,
            user_id=user.pk,
        )
        if value is sentinel:
            export["incomplete_sections"].append(name)
            continue
        export["sections"][name] = value

    return export
