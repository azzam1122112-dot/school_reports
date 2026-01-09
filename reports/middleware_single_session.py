from django.contrib.auth import logout


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
        return self.get_response(request)
