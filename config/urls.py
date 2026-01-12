# config/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from django.contrib.staticfiles import finders
from django.contrib.staticfiles.storage import staticfiles_storage
from django.http import HttpResponse
from django.views.decorators.cache import cache_control
from django.views.generic.base import RedirectView


@cache_control(no_cache=True, must_revalidate=True, max_age=0)
def service_worker(request):
    """Serve service worker from site root to allow scope="/"."""
    content = None
    # Production/collected static
    try:
        with staticfiles_storage.open("sw.js") as fp:
            content = fp.read()
    except Exception:
        content = None

    # Development fallback (no collectstatic): resolve from STATICFILES_DIRS / app static
    if content is None:
        path = finders.find("sw.js")
        if path:
            with open(path, "rb") as fp:
                content = fp.read()

    if content is None:
        return HttpResponse("Service worker not found.", status=404, content_type="text/plain")

    return HttpResponse(content, content_type="application/javascript")


def robots_txt(_request):
    try:
        content = None
        try:
            with staticfiles_storage.open("robots.txt") as fp:
                content = fp.read()
        except Exception:
            content = None

        if content is None:
            return HttpResponse("User-agent: *\nDisallow:\n", content_type="text/plain")

        if isinstance(content, bytes):
            content = content.decode("utf-8", errors="ignore")
        return HttpResponse(content, content_type="text/plain")
    except Exception:
        return HttpResponse("User-agent: *\nDisallow:\n", content_type="text/plain")

urlpatterns = [
    path("admin-panel/", admin.site.urls),
    path(
        "favicon.ico",
        RedirectView.as_view(url=staticfiles_storage.url("favicon.ico"), permanent=True),
    ),
    path("robots.txt", robots_txt),
    path("sw.js", service_worker, name="service_worker"),
    path("", include("reports.urls")),  # واجهة المشروع الأساسية
]

# ✅ أثناء التطوير فقط: نخدم الملفات الثابتة والوسائط من Django مباشرة
if settings.DEBUG:
    urlpatterns += static(settings.STATIC_URL, document_root=settings.BASE_DIR / "static")
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
