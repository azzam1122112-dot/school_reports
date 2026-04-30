from __future__ import annotations

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.test import TransactionTestCase, override_settings
from unittest.mock import AsyncMock, patch

from channels.testing import WebsocketCommunicator

from reports.consumers import NotificationCountsConsumer
from reports.models import Role, School, Teacher


class _DummySession(dict):
    session_key = "test-session-key"


@override_settings(
    CHANNEL_LAYERS={"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}},
)
class NotificationConsumerTests(TransactionTestCase):
    reset_sequences = True

    def setUp(self):
        self.teacher_role, _ = Role.objects.get_or_create(
            slug="teacher",
            defaults={"name": "Teacher"},
        )
        self.school = School.objects.create(name="WS School", code="ws-school")
        self.teacher = Teacher.objects.create_user(
            phone="511111111",
            name="WS Teacher",
            password="pass",
            role=self.teacher_role,
        )

    def _communicator(self, user):
        communicator = WebsocketCommunicator(
            NotificationCountsConsumer.as_asgi(),
            "/ws/notifications/",
        )
        communicator.scope["user"] = user
        communicator.scope["session"] = _DummySession(active_school_id=self.school.id)
        return communicator

    def test_rejects_unauthenticated_before_accept(self):
        communicator = self._communicator(AnonymousUser())
        connected, close_code = async_to_sync(communicator.connect)()
        self.assertFalse(connected)
        self.assertEqual(close_code, 4401)

    def test_authenticated_connection_accepts(self):
        with patch.object(
            NotificationCountsConsumer,
            "_compute_counts_cached",
            new=AsyncMock(return_value={"count": 3, "unread": 2, "signatures_pending": 1}),
        ):
            communicator = self._communicator(self.teacher)
            connected, _ = async_to_sync(communicator.connect)()
            self.assertTrue(connected)

    def test_ping_message_returns_pong(self):
        consumer = NotificationCountsConsumer()
        consumer.last_client_activity_ts = 0.0
        consumer.send_json = AsyncMock()

        async_to_sync(consumer.receive_json)({"type": "ping"})

        consumer.send_json.assert_awaited_once_with({"type": "pong"})
