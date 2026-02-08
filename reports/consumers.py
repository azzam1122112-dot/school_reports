from __future__ import annotations

from typing import Any, Dict, Optional

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.db.models import Count, Q
from django.utils import timezone

from .models import NotificationRecipient


class NotificationCountsConsumer(AsyncJsonWebsocketConsumer):
    """Push unread/signature counters via WebSocket.

    Contract:
    - On connect: send a full `counts` payload.
    - On server-side events: send `delta` payloads (no polling).
    - Client may send {"type": "resync"} to force recompute (non-periodic).
    """

    user_id: int
    active_school_id: Optional[int]

    async def connect(self):
        user = self.scope.get("user")
        if not user or not getattr(user, "is_authenticated", False):
            await self.close(code=4401)
            return

        self.user_id = int(getattr(user, "id", 0) or 0)

        # Session is available when using AuthMiddlewareStack.
        sid = None
        try:
            sess = self.scope.get("session")
            if sess is not None:
                sid = sess.get("active_school_id")
        except Exception:
            sid = None

        try:
            self.active_school_id = int(sid) if sid else None
        except Exception:
            self.active_school_id = None

        # Channels group names must be ASCII alphanumerics/hyphen/underscore/period only.
        self.group_name = f"notif.u{self.user_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)

        await self.accept()

        payload = await self._compute_counts()
        await self.send_json({"type": "counts", **payload})

    async def disconnect(self, code):
        try:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception:
            pass

    async def receive_json(self, content: Dict[str, Any], **kwargs):
        msg_type = (content or {}).get("type")
        if msg_type == "resync":
            payload = await self._compute_counts()
            await self.send_json({"type": "counts", **payload})
            return

        if msg_type == "set_active_school":
            sid = content.get("active_school_id")
            try:
                self.active_school_id = int(sid) if sid else None
            except Exception:
                self.active_school_id = None
            payload = await self._compute_counts()
            await self.send_json({"type": "counts", **payload})
            return

    async def notif_delta(self, event: Dict[str, Any]):
        """Group event handler."""
        out = {
            "type": "delta",
            "delta_unread": int(event.get("delta_unread") or 0),
            "delta_signatures_pending": int(event.get("delta_signatures_pending") or 0),
            "delta_count": int(event.get("delta_count") or 0),
            "notification_school_id": event.get("notification_school_id"),
            "force_resync": bool(event.get("force_resync") or False),
        }
        await self.send_json(out)

    @database_sync_to_async
    def _compute_counts(self) -> Dict[str, int]:
        now = timezone.now()

        qs = NotificationRecipient.objects.filter(teacher_id=self.user_id)

        # Active-school isolation (include global notifications school=NULL)
        if self.active_school_id is not None:
            qs = qs.filter(Q(notification__school_id=self.active_school_id) | Q(notification__school__isnull=True))

        # Exclude expired
        qs = qs.filter(Q(notification__expires_at__gt=now) | Q(notification__expires_at__isnull=True))

        unread_q = Q(is_read=False) & Q(notification__requires_signature=False)
        pending_sig_q = Q(notification__requires_signature=True, is_signed=False)
        attention_q = unread_q | pending_sig_q

        agg = qs.aggregate(
            count=Count("id", filter=attention_q),
            unread=Count("id", filter=unread_q),
            signatures_pending=Count("id", filter=pending_sig_q),
        )

        return {
            "count": int(agg.get("count") or 0),
            "unread": int(agg.get("unread") or 0),
            "signatures_pending": int(agg.get("signatures_pending") or 0),
        }
