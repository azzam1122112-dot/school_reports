from __future__ import annotations

from django.contrib.auth.signals import user_logged_in, user_logged_out
from django.dispatch import receiver


@receiver(user_logged_in)
def _single_session_on_login(sender, request, user, **kwargs):
    """Ensure a user can only have one active session.

    On login:
    - Ensure the current session has a key.
    - Delete the previously recorded session (if any).
    - Persist the new session key on the user.

    Notes:
    - This relies on the DB-backed session engine (default). If a different
      session backend is used, deleting the old session may be a no-op.
    """
    try:
        if request.session.session_key is None:
            request.session.save()
        new_key = request.session.session_key or ""
    except Exception:
        return

    try:
        old_key = getattr(user, "current_session_key", "") or ""
    except Exception:
        old_key = ""

    if old_key and new_key and old_key != new_key:
        try:
            from django.contrib.sessions.models import Session

            Session.objects.filter(session_key=old_key).delete()
        except Exception:
            # If sessions aren't DB-backed, we can't force-delete the old one.
            pass

    try:
        if getattr(user, "current_session_key", "") != new_key:
            user.current_session_key = new_key
            user.save(update_fields=["current_session_key"])
    except Exception:
        pass


@receiver(user_logged_out)
def _single_session_on_logout(sender, request, user, **kwargs):
    """Clear recorded session key when the active session logs out."""
    if not user:
        return

    try:
        sk = request.session.session_key or ""
    except Exception:
        sk = ""

    try:
        if sk and getattr(user, "current_session_key", "") == sk:
            user.current_session_key = ""
            user.save(update_fields=["current_session_key"])
    except Exception:
        pass
