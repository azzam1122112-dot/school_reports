from __future__ import annotations

import hashlib

from django.conf import settings
from django.utils import timezone
from rest_framework import authentication, exceptions

from .models import MobileAccessToken


class OperationsTokenAuthentication(authentication.BaseAuthentication):
    keyword = "Ops-Token"

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        if not header:
            return None
        parts = header.split()
        if len(parts) != 2 or parts[0].lower() != self.keyword.lower():
            return None
        raw = parts[1]
        if not raw.startswith("ops_"):
            raise exceptions.AuthenticationFailed("رمز الوصول غير صالح.")
        if not request.is_secure() and not settings.DEBUG:
            raise exceptions.AuthenticationFailed("يتطلب التطبيق اتصال HTTPS.")
        token = MobileAccessToken.objects.select_related("user").filter(
            token_hash=hashlib.sha256(raw.encode("utf-8")).hexdigest()
        ).first()
        if token is None or not token.is_usable() or not token.user.is_superuser:
            raise exceptions.AuthenticationFailed("رمز الوصول منتهي أو غير صالح.")
        MobileAccessToken.objects.filter(pk=token.pk).update(last_used_at=timezone.now())
        request.operations_token = token
        return token.user, token

    def authenticate_header(self, request):
        return self.keyword
