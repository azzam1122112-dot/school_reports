from django.http import HttpResponseNotFound

BLOCKED_PREFIXES = (
    "/wp-admin",
    "/wordpress",
    "/.env",
    "/lander",
    "/cmd_sco",
)


class BlockBadPathsMiddleware:
    """Blocks common scanner/probe paths early before reaching views/DB."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        path = request.path or "/"
        for pref in BLOCKED_PREFIXES:
            if path.startswith(pref):
                return HttpResponseNotFound()
        return self.get_response(request)
