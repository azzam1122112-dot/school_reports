# config/asgi.py
"""
ASGI config.

- HTTP is served by Django (standard ASGI application).
- WebSocket routes are served by Django Channels.

Production-ready notes:
- Uses AuthMiddlewareStack to attach the logged-in Django user to ws scope.
- Exposes a safe fallback if websocket_urlpatterns can't be imported.
"""

from __future__ import annotations

import os
import logging

from django.core.asgi import get_asgi_application
from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter

logger = logging.getLogger(__name__)

# Ensure settings are loaded
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

# Django ASGI app (handles HTTP)
django_asgi_app = get_asgi_application()


def _load_websocket_urlpatterns():
    """
    Load websocket_urlpatterns from your routing module.
    Keep this defensive so a missing routing file won't break the whole deploy.
    """
    try:
        # ✅ expected: reports/routing.py defines websocket_urlpatterns = [...]
        from reports.routing import websocket_urlpatterns  # type: ignore

        if websocket_urlpatterns is None:
            logger.warning("reports.routing.websocket_urlpatterns is None; treating as empty list.")
            return []
        return websocket_urlpatterns
    except Exception as exc:
        # IMPORTANT: do not crash the app for HTTP if WS isn't configured yet.
        logger.warning("WebSocket routing not loaded; WS will be disabled. Reason: %s", exc)
        return []


websocket_urlpatterns = _load_websocket_urlpatterns()

# Main ASGI application
application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # ✅ WebSocket enabled (if websocket_urlpatterns is non-empty)
        "websocket": AuthMiddlewareStack(
            URLRouter(websocket_urlpatterns)
        ),
    }
)
