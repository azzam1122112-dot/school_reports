import json
from channels.generic.websocket import AsyncWebsocketConsumer


class NotificationsConsumer(AsyncWebsocketConsumer):
    """Legacy consumer — kept for backwards compatibility.

    The actual notification logic is in reports.consumers.NotificationCountsConsumer
    which uses the ``notif.u{id}`` group prefix.  This consumer previously used
    ``notifications_user_{id}`` which is a different group, meaning pushes from
    ``realtime_notifications`` never reached clients connected here.

    Now both consumers use the same group prefix so they stay in sync.
    """

    async def connect(self):
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close(code=4001)
            return

        self.group_name = f"notif.u{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)

        await self.accept()

        # رسالة اختبار أولية (تأكد أنها تظهر في Console)
        await self.send(text_data=json.dumps({"type": "connected", "ok": True}))

    async def disconnect(self, close_code):
        user = self.scope.get("user")
        if user and not user.is_anonymous:
            await self.channel_layer.group_discard(
                f"notif.u{user.id}",
                self.channel_name
            )

    async def notify(self, event):
        # event["payload"] = {...}
        await self.send(text_data=json.dumps(event.get("payload", {})))

    async def notif_delta(self, event):
        """Handle delta events from realtime_notifications."""
        await self.send(text_data=json.dumps(event))
