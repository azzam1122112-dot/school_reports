from __future__ import annotations

from typing import Any, Dict, Optional

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.core.cache import cache
from django.db.models import Count, Q
from django.utils import timezone

import logging
import time

from .models import NotificationRecipient
from core import opmetrics


logger = logging.getLogger(__name__)

# ── Per-user connection limits ──────────────────────────────────────
# At 50K+ schools × 25 teachers, uncontrolled reconnect storms will
# overwhelm Redis pub/sub and the DB.  These limits keep a single user
# from consuming unbounded resources.
MAX_WS_CONNECTIONS_PER_USER = 3       # max concurrent sockets per user
WS_CONNECT_RATE_WINDOW_SECONDS = 60   # sliding window for rate-limit
WS_CONNECT_RATE_MAX = 10              # max new connections per window
COUNTS_CACHE_TTL_SECONDS = 10         # cache _compute_counts per user


class NotificationCountsConsumer(AsyncJsonWebsocketConsumer):
    """Push unread/signature counters via WebSocket.

    Contract:
    - On connect: send a full `counts` payload.
    - On server-side events: send `delta` payloads (no polling).
    - Client may send {"type": "resync"} to force recompute (non-periodic).
    """

    user_id: int
    active_school_id: Optional[int]
    trace_id: str
    _last_resync_ts: float
    group_name: str

    async def connect(self):
        user = self.scope.get("user")
        self.trace_id = self._scope_header("x-request-id") or "-"
        self._last_resync_ts = 0.0
        self.group_name = ""  # ensure always set before disconnect
        session_key = self._scope_session_key()
        path = self.scope.get("path")
        if not user or not getattr(user, "is_authenticated", False):
            opmetrics.increment("ws.notifications.denied.unauthenticated")
            logger.warning(
                "WS notifications reject unauthenticated trace_id=%s ip=%s session=%s path=%s",
                self.trace_id,
                self.scope.get("client")[0] if self.scope.get("client") else "unknown",
                session_key,
                path,
            )
            await self.close(code=4401)
            return

        self.user_id = int(getattr(user, "id", 0) or 0)

        # ── Rate-limit new connections per user ─────────────────────
        rate_key = f"ws:conn_rate:{self.user_id}"
        try:
            if not cache.add(rate_key, 0, timeout=WS_CONNECT_RATE_WINDOW_SECONDS):
                conn_count = cache.incr(rate_key)
            else:
                conn_count = 1
            if conn_count > WS_CONNECT_RATE_MAX:
                opmetrics.increment("ws.notifications.denied.rate_limited")
                logger.warning(
                    "WS notifications rate-limited trace_id=%s user_id=%s connects_in_window=%s",
                    self.trace_id, self.user_id, conn_count,
                )
                await self.close(code=4429)
                return
        except Exception:
            pass  # cache down — allow the connection

        # ── Cap concurrent connections per user ─────────────────────
        cap_key = f"ws:conn_cap:{self.user_id}"
        try:
            cache.add(cap_key, 0, timeout=3600)
            active = cache.incr(cap_key)
            if active > MAX_WS_CONNECTIONS_PER_USER:
                cache.decr(cap_key)
                opmetrics.increment("ws.notifications.denied.max_connections")
                logger.info(
                    "WS notifications max-connections trace_id=%s user_id=%s active=%s",
                    self.trace_id, self.user_id, active,
                )
                await self.close(code=4429)
                return
        except Exception:
            pass  # cache down — allow the connection

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
            # Accept first so we can send a proper close frame, otherwise the
            # browser receives a raw TCP drop and logs 1006 instead of 1011.
            try:
                await self.accept()
            except Exception:
                pass
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
        logger.debug(
            "WS notifications accepted trace_id=%s user_id=%s group=%s active_school_id=%s ip=%s session=%s path=%s",
            self.trace_id,
            self.user_id,
            self.group_name,
            self.active_school_id,
            self.scope.get("client")[0] if self.scope.get("client") else "unknown",
            session_key,
            path,
        )
        opmetrics.increment("ws.notifications.connect.accepted")
        payload = await self._compute_counts_cached()
        await self.send_json({"type": "counts", **payload})

    async def disconnect(self, code):
        group = getattr(self, "group_name", "")
        if group:
            try:
                await self.channel_layer.group_discard(group, self.channel_name)
            except Exception:
                pass
        # Release connection-cap slot
        try:
            uid = getattr(self, "user_id", None)
            if uid is not None:
                cap_key = f"ws:conn_cap:{uid}"
                cache.decr(cap_key)
        except Exception:
            pass
        try:
            user = getattr(self, "user_id", None)
            path = self.scope.get("path")
            session_key = self._scope_session_key()
            trace_id = getattr(self, "trace_id", "-")
            # code=None means the TCP connection dropped with no WebSocket close
            # frame (e.g. page navigation, OS killing idle tab) — treat it as 1006.
            norm_code = (
                int(code)
                if isinstance(code, int)
                else (int(code) if isinstance(code, str) and code.isdigit() else 1006)
            )
            if norm_code == 1000:
                logger.debug("WS notifications close trace_id=%s user_id=%s session=%s path=%s code=1000", trace_id, user, session_key, path)
            elif norm_code == 1006:
                opmetrics.increment("ws.notifications.close.abnormal_1006")
                ua = self._scope_header("user-agent")
                ua_l = ua.lower()
                is_mobile_browser = "android" in ua_l or "mobile" in ua_l or "iphone" in ua_l or "ipad" in ua_l
                bucket = timezone.now().strftime("%Y%m%d%H")
                key = f"ws_disconnect_1006:{bucket}"
                try:
                    cache.add(key, 0, timeout=7200)
                    count = cache.incr(key)
                except Exception:
                    count = None
                # Log only 1st, 10th, 100th per hour to reduce noise
                if count in {1, 10, 100} or count is None:
                    log_fn = logger.debug if is_mobile_browser else logger.warning
                    log_fn(
                        "WS notifications abnormal_close trace_id=%s user_id=%s session=%s path=%s code=%s ua=%s hour_count=%s",
                        trace_id, user, session_key, path, code, ua, count)
            elif norm_code == 4401:
                opmetrics.increment("ws.notifications.close.unauthorized_4401")
                logger.warning("WS notifications close trace_id=%s user_id=%s session=%s path=%s code=4401", trace_id, user, session_key, path)
            else:
                opmetrics.increment("ws.notifications.close.other")
                logger.info("WS notifications close trace_id=%s user_id=%s session=%s path=%s code=%s normalized_code=%s", trace_id, user, session_key, path, code, norm_code)
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
            # Throttle resyncs: max once per 5 seconds to avoid DB hammering
            now = time.monotonic()
            if now - self._last_resync_ts < 5.0:
                return
            self._last_resync_ts = now
            logger.debug("WS notifications resync request trace_id=%s user_id=%s", getattr(self, "trace_id", "-"), getattr(self, "user_id", None))
            payload = await self._compute_counts_cached()
            await self.send_json({"type": "counts", **payload})
            return

        if msg_type == "set_active_school":
            sid = content.get("active_school_id")
            try:
                self.active_school_id = int(sid) if sid else None
            except Exception:
                self.active_school_id = None
            logger.info(
                "WS notifications active school updated trace_id=%s user_id=%s active_school_id=%s",
                getattr(self, "trace_id", "-"),
                getattr(self, "user_id", None),
                self.active_school_id,
            )
            payload = await self._compute_counts_cached()
            await self.send_json({"type": "counts", **payload})
            return

    async def notif_delta(self, event: Dict[str, Any]):
        """Group event handler."""
        logger.debug(
            "WS notifications delta event trace_id=%s user_id=%s event_trace_id=%s school_id=%s",
            getattr(self, "trace_id", "-"),
            getattr(self, "user_id", None),
            event.get("trace_id"),
            event.get("notification_school_id"),
        )
        out = {
            "type": "delta",
            "delta_unread": int(event.get("delta_unread") or 0),
            "delta_signatures_pending": int(event.get("delta_signatures_pending") or 0),
            "delta_count": int(event.get("delta_count") or 0),
            "notification_school_id": event.get("notification_school_id"),
            "force_resync": bool(event.get("force_resync") or False),
        }
        await self.send_json(out)

    async def _compute_counts_cached(self) -> Dict[str, int]:
        """Return cached notification counts to avoid hammering the DB on
        reconnect storms.  At 50K+ schools the same user may reconnect
        dozens of times per minute (mobile browser 1006)."""
        uid = getattr(self, "user_id", 0)
        sid = getattr(self, "active_school_id", None) or 0
        ck = f"ws:counts:{uid}:{sid}"
        try:
            cached = cache.get(ck)
            if cached is not None:
                return cached
        except Exception:
            pass
        result = await self._compute_counts()
        try:
            cache.set(ck, result, COUNTS_CACHE_TTL_SECONDS)
        except Exception:
            pass
        return result

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

    def _scope_session_key(self) -> str:
        try:
            sess = self.scope.get("session")
            if sess is None:
                return "none"
            return getattr(sess, "session_key", None) or "none"
        except Exception:
            return "none"
