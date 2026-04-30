from __future__ import annotations

from pathlib import Path

from asgiref.sync import async_to_sync
from django.contrib.auth.models import AnonymousUser
from django.test import SimpleTestCase, TransactionTestCase, override_settings
from unittest.mock import AsyncMock, patch

from channels.testing import WebsocketCommunicator

from reports.consumers import NotificationCountsConsumer
from reports.models import Role, School, Teacher


class _DummySession(dict):
    session_key = "test-session-key"


class NotificationFrontendAssetsTests(SimpleTestCase):
    def test_base_template_includes_notifications_manager_once(self):
        base_template = Path("reports/templates/base.html").read_text(encoding="utf-8")
        self.assertEqual(base_template.count("js/notifications-ws-manager.js"), 1)

    def test_notifications_manager_keeps_singleton_guard_and_navigation_reason(self):
        js_source = Path("static/js/notifications-ws-manager.js").read_text(encoding="utf-8")
        self.assertIn("window.NotificationSocketManager", js_source)
        self.assertIn("code === 1001", js_source)
        self.assertIn("return 'navigation';", js_source)


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

    def test_set_active_school_does_not_log_when_value_unchanged(self):
        consumer = NotificationCountsConsumer()
        consumer.trace_id = "trace-1"
        consumer.user_id = self.teacher.id
        consumer.active_school_id = self.school.id
        consumer.scope = {"session": _DummySession(active_school_id=self.school.id), "user": self.teacher}
        consumer.send_json = AsyncMock()
        consumer._compute_counts_cached = AsyncMock(
            return_value={"count": 0, "unread": 0, "signatures_pending": 0}
        )

        with patch("reports.consumers.logger.info") as log_info:
            async_to_sync(consumer.receive_json)(
                {"type": "set_active_school", "active_school_id": self.school.id}
            )

        log_info.assert_not_called()
        consumer.send_json.assert_awaited_once_with(
            {"type": "counts", "count": 0, "unread": 0, "signatures_pending": 0}
        )

    def test_set_active_school_is_clamped_to_session_school(self):
        other_school = School.objects.create(name="Other School", code="other-school")
        consumer = NotificationCountsConsumer()
        consumer.trace_id = "trace-4"
        consumer.user_id = self.teacher.id
        consumer.active_school_id = self.school.id
        consumer.scope = {"session": _DummySession(active_school_id=self.school.id), "user": self.teacher}
        consumer.send_json = AsyncMock()
        consumer._compute_counts_cached = AsyncMock(
            return_value={"count": 1, "unread": 1, "signatures_pending": 0}
        )

        async_to_sync(consumer.receive_json)(
            {"type": "set_active_school", "active_school_id": other_school.id}
        )

        self.assertEqual(consumer.active_school_id, self.school.id)
        consumer.send_json.assert_awaited_once_with(
            {"type": "counts", "count": 1, "unread": 1, "signatures_pending": 0}
        )

    def test_disconnect_1006_logs_warning_only_when_repeated(self):
        consumer = NotificationCountsConsumer()
        consumer.user_id = self.teacher.id
        consumer.trace_id = "trace-2"
        consumer.scope = {
            "path": "/ws/notifications/",
            "headers": [(b"user-agent", b"Mozilla/5.0")],
        }
        consumer.connected_at = 0.0
        consumer.group_name = ""
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = "test-channel"
        consumer._idle_watchdog_task = None

        with patch("reports.consumers.timezone.now") as mocked_now:
            mocked_now.return_value.strftime.return_value = "2026043011"
            with patch("reports.consumers.cache.add", return_value=True):
                with patch("reports.consumers.cache.incr", return_value=1):
                    with patch("reports.consumers.time.monotonic", return_value=1.0):
                        with patch("reports.consumers.logger.debug") as log_debug:
                            with patch("reports.consumers.logger.warning") as log_warning:
                                async_to_sync(consumer.disconnect)(1006)
        self.assertTrue(log_debug.called)
        self.assertFalse(log_warning.called)

        consumer = NotificationCountsConsumer()
        consumer.user_id = self.teacher.id
        consumer.trace_id = "trace-3"
        consumer.scope = {
            "path": "/ws/notifications/",
            "headers": [(b"user-agent", b"Mozilla/5.0")],
        }
        consumer.connected_at = 0.0
        consumer.group_name = ""
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = "test-channel"
        consumer._idle_watchdog_task = None

        with patch("reports.consumers.timezone.now") as mocked_now:
            mocked_now.return_value.strftime.return_value = "2026043012"
            with patch("reports.consumers.cache.add", return_value=False):
                with patch("reports.consumers.cache.incr", return_value=10):
                    with patch("reports.consumers.time.monotonic", return_value=1.0):
                        with patch("reports.consumers.logger.debug") as log_debug:
                            with patch("reports.consumers.logger.warning") as log_warning:
                                async_to_sync(consumer.disconnect)(1006)
        self.assertFalse(log_debug.called)
        self.assertTrue(log_warning.called)

    def test_disconnect_1001_is_treated_as_navigation_without_logging(self):
        consumer = NotificationCountsConsumer()
        consumer.user_id = self.teacher.id
        consumer.trace_id = "trace-5"
        consumer.scope = {"path": "/ws/notifications/", "headers": []}
        consumer.connected_at = 0.0
        consumer.group_name = ""
        consumer.channel_layer = AsyncMock()
        consumer.channel_name = "test-channel"
        consumer._idle_watchdog_task = None

        with patch("reports.consumers.time.monotonic", return_value=1.0):
            with patch("reports.consumers.logger.debug") as log_debug:
                with patch("reports.consumers.logger.info") as log_info:
                    with patch("reports.consumers.logger.warning") as log_warning:
                        async_to_sync(consumer.disconnect)(1001)

        self.assertFalse(log_debug.called)
        self.assertFalse(log_info.called)
        self.assertFalse(log_warning.called)
