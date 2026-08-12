# reports/api_auth.py
# -*- coding: utf-8 -*-
"""مصادقة الأنظمة الخارجية بمفتاح تكامل.

**المبدأ الحاكم: المفتاح يدخل من الباب نفسه لا من باب خلفي.**

بعد التحقق يُثبَّت ``request.active_school`` من المفتاح. وهذا السطر الواحد هو
جوهر التصميم: كل ما بُني للمتصفّح — عزلُ المستأجر، ودوالُّ الرؤية، وتقييد
الاستعلامات — يعمل على طلب الـAPI بلا تعديل حرف. والبديل (مسارٌ موازٍ بقواعده
الخاصة) هو بالضبط ما يُنسى تحديثه حين تتغيّر صلاحية، فيصير الـAPI باباً خلفياً
لا يكتشفه أحد.

ولا تُقبل الترويسة إلا على HTTPS في الإنتاج: المفتاح سرٌّ يُرسَل نصاً في كل
طلب، وإرساله على HTTP تسليمٌ له لكل من في الطريق.
"""
from __future__ import annotations

from django.conf import settings
from django.utils import timezone
from rest_framework import authentication, exceptions, permissions, throttling

from .model_parts.api_keys import API_KEY_PREFIX, hash_api_key
from .models import SchoolApiKey

AUTH_SCHEME = "Api-Key"


class SchoolApiKeyAuthentication(authentication.BaseAuthentication):
    """``Authorization: Api-Key twq_<id>_<secret>``"""

    keyword = AUTH_SCHEME

    def authenticate(self, request):
        header = authentication.get_authorization_header(request).decode("latin-1")
        if not header:
            return None

        parts = header.split()
        if len(parts) != 2 or parts[0].lower() != self.keyword.lower():
            # ترويسة لنظام مصادقة آخر (الجلسة مثلاً): تُترك له.
            return None

        raw_key = parts[1]
        if not raw_key.startswith(f"{API_KEY_PREFIX}_"):
            raise exceptions.AuthenticationFailed("صيغة مفتاح غير صحيحة.")

        if not request.is_secure() and not settings.DEBUG:
            # المفتاح يُرسَل نصاً في كل طلب. قبولُه على HTTP تسليمٌ له.
            raise exceptions.AuthenticationFailed("مفاتيح التكامل تتطلب HTTPS.")

        key = (
            SchoolApiKey.objects.select_related("school", "acting_as")
            .filter(key_hash=hash_api_key(raw_key))
            .first()
        )
        # رسالةٌ واحدة للمفتاح المجهول وللمعطَّل وللمنتهي: التفريق بينها يُخبر
        # المهاجم أي مفتاح كان صحيحاً يوماً.
        if key is None or not key.is_usable():
            raise exceptions.AuthenticationFailed("مفتاح غير صالح أو منتهٍ.")

        # المدرسة تُثبَّت من المفتاح لا من الطلب — فيمرّ على طبقة العزل نفسها.
        request.active_school = key.school
        request.api_key = key

        # ``update`` لا ``save``: لا إشارات ولا سباق كتابةٍ على صفٍّ ساخن.
        SchoolApiKey.objects.filter(pk=key.pk).update(last_used_at=timezone.now())

        return (key.acting_as, key)

    def authenticate_header(self, request):
        # بدونها يردّ DRF بـ403 بدل 401، فلا يعرف العميل أن عليه المصادقة.
        return self.keyword


class HasWriteScope(permissions.BasePermission):
    """الكتابة تحتاج نطاق كتابة صريحاً.

    القراءة هي الافتراض لأن أغلب التكاملات تقرأ فقط، ومفتاحٌ يكتب بلا داعٍ
    خسارةٌ محتملة بلا مقابل. وجلسةُ المتصفّح لا يحكمها هذا الشرط — لها
    صلاحيات صاحبها كما هي.
    """

    message = "هذا المفتاح للقراءة فقط."

    def has_permission(self, request, view):
        if request.method in permissions.SAFE_METHODS:
            return True
        key = getattr(request, "api_key", None)
        if key is None:
            return True  # ليست مصادقة مفتاح — تُحكم بقواعدها.
        return bool(key.can_write)


class ApiKeyRateThrottle(throttling.SimpleRateThrottle):
    """حدٌّ مستقل للمفاتيح.

    لا يُخلط بحدّ المستخدم: نظامٌ يزامن كل خمس دقائق سلوكُه مشروع وإيقاعُه
    مختلف تماماً عن إنسانٍ يتصفّح. وخلطُهما يعني إما خنقَ التكامل أو رفعَ
    الحدّ للجميع.
    """

    scope = "api_key"

    def get_cache_key(self, request, view):
        key = getattr(request, "api_key", None)
        if key is None:
            return None
        return self.cache_format % {"scope": self.scope, "ident": key.public_id}
