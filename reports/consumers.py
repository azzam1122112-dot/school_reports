from __future__ import annotations

from typing import Any, Dict, Optional

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

import logging

from .models import NotificationRecipient


logger = logging.getLogger(__name__)


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
            logger.warning(
                "WS notifications reject: unauthenticated | IP=%s | session=%s | path=%s",
                self.scope.get("client")[0] if self.scope.get("client") else "unknown",
                self.scope.get("session").session_key if self.scope.get("session") else "none",
                self.scope.get("path"),
            )
            await self.close(code=4401)
            return

        self.user_id = int(getattr(user, "id", 0) or 0)
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
        self.group_name = f"notif.u{self.user_id}"
        try:
            await self.channel_layer.group_add(self.group_name, self.channel_name)
        except Exception as exc:
            logger.exception(
                "WS notifications group_add failed (user_id=%s group=%s path=%s): %s",
                self.user_id,
                self.group_name,
                self.scope.get("path"),
                exc,
            )
            await self.close(code=1011)
            return
        try:
            await self.accept()
        except Exception as exc:
            logger.exception(
                "WS notifications accept failed (user_id=%s path=%s): %s",
                self.user_id,
                self.scope.get("path"),
                exc,
            )
            return
        logger.info(
            "WS notifications accepted | user_id=%s | group=%s | active_school_id=%s | IP=%s | session=%s | path=%s",
            self.user_id,
            self.group_name,
            self.active_school_id,
            self.scope.get("client")[0] if self.scope.get("client") else "unknown",
            self.scope.get("session").session_key if self.scope.get("session") else "none",
            self.scope.get("path"),
        )
        payload = await self._compute_counts()
        await self.send_json({"type": "counts", **payload})

    async def disconnect(self, code):
        try:
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
        except Exception:
            pass
        try:
            user = getattr(self, "user_id", None)
            path = self.scope.get("path")
            if int(code or 0) == 1000:
                logger.info("WS notifications normal close 1000 | user_id=%s | path=%s", user, path)
            elif int(code or 0) == 1006:
                ua = self._scope_header("user-agent")
                ua_l = ua.lower()
                is_ios_safari = ("iphone" in ua_l or "ipad" in ua_l or "ipod" in ua_l) and ("safari" in ua_l)
                is_mobile_browser = "android" in ua_l or "mobile" in ua_l or is_ios_safari
                bucket = timezone.now().strftime("%Y%m%d%H%M")
                key = f"ws_disconnect_1006:{bucket}"
                try:
                    cache.add(key, 0, timeout=180)
                    count = cache.incr(key)
                except Exception:
                    count = None
                if is_mobile_browser and count is not None and count < 10:
                    log_fn = logger.info
                else:
                    log_fn = logger.warning if (count in {1, 3, 5} or count is None) else logger.info
                log_fn(
                    "WS notifications abnormal_close code=1006 | user_id=%s | path=%s | ua=%s | minute_count=%s",
                    user, path, ua, count)
            elif int(code or 0) == 4401:
                logger.warning("WS notifications close 4401 (unauthenticated) | user_id=%s | path=%s", user, path)
            else:
                logger.info("WS notifications close code=%s | user_id=%s | path=%s", code, user, path)
        except Exception:
            pass

    async def receive_json(self, content: Dict[str, Any], **kwargs):
        msg_type = (content or {}).get("type")
        if msg_type in {"keepalive", "ping"}:
            try:
                await self.send_json({"type": "pong"})
            except Exception:
                pass
            return
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

    def _scope_header(self, name: str) -> str:
        needle = (name or "").strip().lower().encode()
        for item in (self.scope.get("headers") or []):
            try:
                k, v = item
                if k == needle:
                    return v.decode("utf-8", errors="ignore")
            except Exception:
                continue
        return "-"
