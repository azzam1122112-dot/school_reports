# config/urls.py
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static as serve_static
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

    response = HttpResponse(content, content_type="application/javascript")
    response["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


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
        response = HttpResponse(content, content_type="text/plain")
        response["Cache-Control"] = "public, max-age=3600"
        return response
    except Exception:
        response = HttpResponse("User-agent: *\nDisallow:\n", content_type="text/plain")
        response["Cache-Control"] = "public, max-age=3600"
        return response


def security_txt(request):
    base = request.build_absolute_uri("/").rstrip("/")
    content = "\n".join([
        "Contact: mailto:support@example.com",
        f"Canonical: {base}/.well-known/security.txt",
        f"Policy: {base}/privacy-policy/",
        "Preferred-Languages: ar, en",
        "",
    ])
    response = HttpResponse(content, content_type="text/plain")
    response["Cache-Control"] = "public, max-age=3600"
    return response


def sitemap_xml(request):
    base = request.build_absolute_uri("/").rstrip("/")
    urls = [
        f"{base}/",
        f"{base}/login/",
        f"{base}/user-guide/",
        f"{base}/privacy-policy/",
        f"{base}/faq/",
    ]
    body = ['<?xml version="1.0" encoding="UTF-8"?>', '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">']
    for url in urls:
        body.append(f"  <url><loc>{url}</loc></url>")
    body.append("</urlset>")
    response = HttpResponse("\n".join(body), content_type="application/xml")
    response["Cache-Control"] = "public, max-age=3600"
    return response

from core.views import healthz, ops_metrics

urlpatterns = [
    # Health check — lightweight, no auth/session required
    path("healthz/", healthz, name="healthz"),
    # Operational metrics — superuser only
    path("ops/metrics/", ops_metrics, name="ops_metrics"),
    path("admin-panel/", admin.site.urls),
    path(
        "favicon.ico",
        RedirectView.as_view(url="/static/favicon.ico", permanent=True),
    ),
    path(
        "favicon.png",
        RedirectView.as_view(url="/static/favicon.ico", permanent=False),
    ),
    path(
        "touch-icon-iphone.png",
        RedirectView.as_view(url="/static/img/logo1.png", permanent=False),
    ),
    # iOS Safari may request these from site root regardless of <link rel="apple-touch-icon">.
    path(
        "apple-touch-icon.png",
        RedirectView.as_view(url="/static/img/logo1.png", permanent=False),
    ),
    path(
        "apple-touch-icon-precomposed.png",
        RedirectView.as_view(url="/static/img/logo1.png", permanent=False),
    ),
    path(
        "apple-touch-icon-120x120.png",
        RedirectView.as_view(url="/static/img/logo1.png", permanent=False),
    ),
    path(
        "apple-touch-icon-120x120-precomposed.png",
        RedirectView.as_view(url="/static/img/logo1.png", permanent=False),
    ),
    path(
        "apple-touch-icon-152x152.png",
        RedirectView.as_view(url="/static/img/logo1.png", permanent=False),
    ),
    path(
        "apple-touch-icon-152x152-precomposed.png",
        RedirectView.as_view(url="/static/img/logo1.png", permanent=False),
    ),
    path(
        "apple-touch-icon-167x167.png",
        RedirectView.as_view(url="/static/img/logo1.png", permanent=False),
    ),
    path(
        "apple-touch-icon-167x167-precomposed.png",
        RedirectView.as_view(url="/static/img/logo1.png", permanent=False),
    ),
    path(
        "apple-touch-icon-180x180.png",
        RedirectView.as_view(url="/static/img/logo1.png", permanent=False),
    ),
    path(
        "apple-touch-icon-180x180-precomposed.png",
        RedirectView.as_view(url="/static/img/logo1.png", permanent=False),
    ),
    path("robots.txt", robots_txt),
    path("sitemap.xml", sitemap_xml, name="sitemap_xml"),
    path(".well-known/security.txt", security_txt, name="security_txt"),
    path("sw.js", service_worker, name="service_worker"),
    # REST API v1
    path("api/v1/", include("reports.api_urls")),
    path("", include("reports.urls")),  # واجهة المشروع الأساسية
]

# ✅ أثناء التطوير فقط: نخدم الملفات الثابتة والوسائط من Django مباشرة
if settings.DEBUG:
    urlpatterns += serve_static(settings.STATIC_URL, document_root=settings.BASE_DIR / "static")
    urlpatterns += serve_static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
