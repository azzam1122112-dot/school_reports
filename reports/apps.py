from django.apps import AppConfig


class ReportsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'reports'

    def ready(self):
        # Register auth/session signals (single-session enforcement, etc.)
        try:
            from . import signals  # noqa: F401
        except Exception:
            pass
