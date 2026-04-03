from __future__ import annotations

from typing import Iterable, List, Optional
import logging

from django.utils import timezone


logger = logging.getLogger(__name__)

# ── Batch size for group_send calls ─────────────────────────────────
# At 50K schools × 25 teachers, pushing individually causes 1.25M
# async_to_sync(group_send) calls per broadcast notification.
# Batching reduces the async_to_sync overhead dramatically.
_WS_PUSH_BATCH_SIZE = 200


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


def _push_delta_to_users_batch(
    *,
    teacher_ids: List[int],
    notification_school_id: Optional[int],
    delta_unread: int = 0,
    delta_signatures_pending: int = 0,
    delta_count: int = 0,
    trace_id: str | None = None,
) -> int:
    """Push the same delta message to many users efficiently using a
    single async event loop instead of one async_to_sync per user."""
    channel_layer = _get_channel_layer()
    if channel_layer is None:
        return 0

    import asyncio

    msg = {
        "type": "notif_delta",
        "delta_unread": int(delta_unread),
        "delta_signatures_pending": int(delta_signatures_pending),
        "delta_count": int(delta_count),
        "notification_school_id": notification_school_id,
        "force_resync": False,
        "trace_id": trace_id,
    }

    async def _send_batch(ids: List[int]):
        tasks = [
            channel_layer.group_send(f"notif.u{tid}", msg)
            for tid in ids
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return sum(1 for r in results if not isinstance(r, Exception))

    sent = 0
    try:
        from asgiref.sync import async_to_sync

        for i in range(0, len(teacher_ids), _WS_PUSH_BATCH_SIZE):
            batch = teacher_ids[i:i + _WS_PUSH_BATCH_SIZE]
            sent += async_to_sync(_send_batch)(batch)
    except Exception:
        logger.warning(
            "WS batch push failed trace_id=%s total=%s sent=%s",
            trace_id, len(teacher_ids), sent, exc_info=True,
        )

    return sent


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

    # Collect valid teacher IDs
    valid_ids = []
    for tid in teacher_ids:
        try:
            valid_ids.append(int(tid))
        except Exception:
            continue

    if not valid_ids:
        return

    # Use batched push instead of per-user loop
    _push_delta_to_users_batch(
        teacher_ids=valid_ids,
        notification_school_id=notification_school_id,
        delta_unread=du,
        delta_signatures_pending=ds,
        delta_count=dc,
        trace_id=trace_id,
    )
