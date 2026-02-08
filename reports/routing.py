from __future__ import annotations

from django.urls import re_path

from . import consumers

websocket_urlpatterns = [
    # Be tolerant: depending on server/router versions the ASGI scope path may
    # include a leading slash. Support both to avoid silent handshake rejection.
    re_path(r"^/?ws/notifications/?$", consumers.NotificationCountsConsumer.as_asgi()),
]
