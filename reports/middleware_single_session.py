from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect


class EnforceSingleSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, "user", None)
        if user and user.is_authenticated:
            current = getattr(user, "current_session_key", "") or ""
            sk = request.session.session_key or ""
            if current and sk and current != sk:
                logout(request)
                try:
                    accept = (request.headers.get("Accept") or "").lower()
                    xrw = (request.headers.get("X-Requested-With") or "").lower()
                    wants_json = "application/json" in accept or xrw == "xmlhttprequest"
                except Exception:
                    wants_json = False
                if wants_json:
                    return JsonResponse({"detail": "session_revoked"}, status=401)
                return redirect(settings.LOGIN_URL)
        return self.get_response(request)
