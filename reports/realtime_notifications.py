from __future__ import annotations

from typing import Iterable, Optional
import logging

from django.utils import timezone


logger = logging.getLogger(__name__)


def _get_channel_layer():
    try:
        from channels.layers import get_channel_layer

        return get_channel_layer()
    except Exception:
        return None


def push_delta_to_user(
    *,
    teacher_id: int,
    notification_school_id: Optional[int],
    delta_unread: int = 0,
    delta_signatures_pending: int = 0,
    delta_count: int = 0,
    force_resync: bool = False,
    trace_id: str | None = None,
) -> None:
    channel_layer = _get_channel_layer()
    if channel_layer is None:
        logger.debug(
            "WS push skipped: missing channel layer teacher_id=%s school_id=%s trace_id=%s",
            teacher_id,
            notification_school_id,
            trace_id,
        )
        return

    try:
        from asgiref.sync import async_to_sync

        async_to_sync(channel_layer.group_send)(
            f"notif.u{int(teacher_id)}",
            {
                "type": "notif_delta",
                "delta_unread": int(delta_unread),
                "delta_signatures_pending": int(delta_signatures_pending),
                "delta_count": int(delta_count),
                "notification_school_id": notification_school_id,
                "force_resync": bool(force_resync),
                "trace_id": trace_id,
            },
        )
        logger.debug(
            "WS push sent teacher_id=%s school_id=%s force_resync=%s trace_id=%s",
            teacher_id,
            notification_school_id,
            bool(force_resync),
            trace_id,
        )
    except Exception:
        logger.warning(
            "WS push failed teacher_id=%s school_id=%s force_resync=%s trace_id=%s",
            teacher_id,
            notification_school_id,
            bool(force_resync),
            trace_id,
            exc_info=True,
        )
        return


def push_force_resync(*, teacher_id: int, trace_id: str | None = None) -> None:
    push_delta_to_user(
        teacher_id=teacher_id,
        notification_school_id=None,
        delta_unread=0,
        delta_signatures_pending=0,
        delta_count=0,
        force_resync=True,
        trace_id=trace_id,
    )


def push_new_notification_to_teachers(*, notification, teacher_ids: Iterable[int], trace_id: str | None = None) -> None:
    """Push a +1 delta for a newly-created recipient row.

    NOTE: This is used after bulk_create(), because bulk_create doesn't trigger post_save signals.
    """

    try:
        if getattr(notification, "expires_at", None) is not None and notification.expires_at <= timezone.now():
            return
    except Exception:
        pass

    try:
        requires_signature = bool(getattr(notification, "requires_signature", False))
    except Exception:
        requires_signature = False

    try:
        notification_school_id = getattr(notification, "school_id", None)
    except Exception:
        notification_school_id = None

    if requires_signature:
        du, ds, dc = 0, 1, 1
    else:
        du, ds, dc = 1, 0, 1

    for tid in teacher_ids:
        try:
            push_delta_to_user(
                teacher_id=int(tid),
                notification_school_id=notification_school_id,
                delta_unread=du,
                delta_signatures_pending=ds,
                delta_count=dc,
                trace_id=trace_id,
            )
        except Exception:
            continue
