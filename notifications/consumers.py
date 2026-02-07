import json
from channels.generic.websocket import AsyncWebsocketConsumer


class NotificationsConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or user.is_anonymous:
            await self.close(code=4001)
            return

        self.group_name = f"notifications_user_{user.id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)

        await self.accept()

        # رسالة اختبار أولية (تأكد أنها تظهر في Console)
        await self.send(text_data=json.dumps({"type": "connected", "ok": True}))

    async def disconnect(self, close_code):
        user = self.scope.get("user")
        if user and not user.is_anonymous:
            await self.channel_layer.group_discard(
                f"notifications_user_{user.id}",
                self.channel_name
            )

    async def notify(self, event):
        # event["payload"] = {...}
        await self.send(text_data=json.dumps(event.get("payload", {})))
