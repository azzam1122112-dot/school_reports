"""ASGI config.

HTTP continues to be served by Django; WebSocket routes are handled by Django Channels.
"""

import os

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")

django_asgi_app = get_asgi_application()

try:
	from reports.routing import websocket_urlpatterns
except Exception:
	websocket_urlpatterns = []

application = ProtocolTypeRouter(
	{
		"http": django_asgi_app,
		"websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
	}
)
