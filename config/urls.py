# config/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpResponse
from django.views.decorators.cache import cache_control


@cache_control(no_cache=True, must_revalidate=True, max_age=0)
def service_worker(request):
    """Serve service worker from site root to allow scope="/"."""
    with staticfiles_storage.open("sw.js") as fp:
        content = fp.read()
    if isinstance(content, bytes):
        return HttpResponse(content, content_type="application/javascript")
    return HttpResponse(content, content_type="application/javascript")

urlpatterns = [
    path("admin-panel/", admin.site.urls),
    path("sw.js", service_worker, name="service_worker"),
    path("", include("reports.urls")),  # واجهة المشروع الأساسية
]

# ✅ أثناء التطوير فقط: نخدم الملفات الثابتة والوسائط من Django مباشرة
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "static")
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
