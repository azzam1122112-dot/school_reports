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

        # Register incremental storage-usage tracking signals.
        try:
            from . import storage_tracking

            storage_tracking.connect_all()
        except Exception:
            pass

        # Delete replaced/deleted FileField objects from local storage or R2
        # only after the surrounding database transaction commits.
        try:
            from . import file_cleanup

            file_cleanup.connect_all()
        except Exception:
            pass
