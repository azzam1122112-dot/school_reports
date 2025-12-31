from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.contrib import messages

import secrets


import threading

_thread_locals = threading.local()

def get_current_request():
    return getattr(_thread_locals, "request", None)

class AuditLogMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        _thread_locals.request = request
        response = self.get_response(request)
        # تنظيف بعد الطلب
        if hasattr(_thread_locals, "request"):
            del _thread_locals.request
        return response


class IdleLogoutMiddleware:
    """يسجل خروج المستخدم تلقائياً بعد مدة خمول.

    الخمول هنا يعني: عدم وجود تفاعل/تنقل فعلي داخل الصفحة.
    طلبات الخلفية (polling/AJAX/fetch) لا تُحتسب كنشاط.
    """

    SESSION_KEY = "_last_activity_ts"

    def __init__(self, get_response):
        self.get_response = get_response
        self.timeout_seconds = int(getattr(settings, "IDLE_LOGOUT_SECONDS", 30 * 60))

    def _is_interactive_request(self, request) -> bool:
        """Heuristic لتحديد ما إذا كان الطلب ناتجاً عن تفاعل المستخدم.

        - Navigations لصفحات HTML تُحتسب نشاطاً
        - Submits التقليدية للنماذج (form) تُحتسب نشاطاً
        - طلبات الخلفية (XHR/fetch/json) لا تُحتسب
        """

        headers = request.headers
        sec_fetch_mode = (headers.get("Sec-Fetch-Mode") or "").lower()
        sec_fetch_dest = (headers.get("Sec-Fetch-Dest") or "").lower()
        x_requested_with = (headers.get("X-Requested-With") or "").lower()
        accept = (headers.get("Accept") or "").lower()
        content_type = (headers.get("Content-Type") or "").lower()

        is_navigate = sec_fetch_mode == "navigate" or sec_fetch_dest == "document"
        is_xhr = x_requested_with == "xmlhttprequest"
        wants_html = "text/html" in accept
        wants_json = "application/json" in accept

        if is_navigate:
            return True

        # غالباً GET/HEAD الخلفية تكون fetch/XHR أو JSON؛ لا نحتسبها
        if request.method in {"GET", "HEAD"}:
            return wants_html and not wants_json and not is_xhr

        # Submits نماذج HTML التقليدية تُحتسب نشاطاً
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if content_type.startswith("application/x-www-form-urlencoded"):
                return True
            if content_type.startswith("multipart/form-data"):
                return True

        return False

    def _is_background_request(self, request) -> bool:
        return not self._is_interactive_request(request)

    def __call__(self, request):
        # السماح بالملفات الثابتة والوسائط بدون احتسابها كنشاط
        if request.path.startswith("/static/") or request.path.startswith("/media/"):
            return self.get_response(request)

        # لو غير مسجل دخول، لا شيء نفعله
        if not request.user.is_authenticated:
            return self.get_response(request)

        # لا نطبق فحص الخمول على صفحة تسجيل الدخول/الخروج لتجنب أي حلقات
        try:
            login_path = reverse("reports:login")
            logout_path = reverse("reports:logout")
            if request.path in {login_path, logout_path}:
                return self.get_response(request)
        except Exception:
            pass

        now_ts = timezone.now().timestamp()
        last_ts = request.session.get(self.SESSION_KEY)

        if last_ts is not None:
            try:
                last_ts_f = float(last_ts)
                if now_ts - last_ts_f > self.timeout_seconds:
                    # logout() ينهى الجلسة (flush) ويُسقط المستخدم
                    logout(request)
                    if self._is_background_request(request):
                        return JsonResponse({"detail": "session_expired"}, status=401)
                    return redirect(settings.LOGIN_URL)
            except Exception:
                # في حال كانت القيمة غير صالحة لأي سبب، نعيد ضبطها
                pass

        # تحديث النشاط فقط لو كان تفاعل فعلي (لا نحتسب polling/AJAX كنشاط)
        if self._is_interactive_request(request):
            request.session[self.SESSION_KEY] = now_ts
            request.session.set_expiry(self.timeout_seconds)
        return self.get_response(request)

class SubscriptionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        # 1) تجاوز الفحص للمستخدمين غير المسجلين أو المدراء النظام (Superusers)
        if not request.user.is_authenticated or getattr(request.user, "is_superuser", False):
            return self.get_response(request)

        # 2) السماح بالملفات الثابتة والوسائط
        if request.path.startswith('/static/') or request.path.startswith('/media/'):
            return self.get_response(request)

        # 3) تحديد المسارات المسموح بها عند انتهاء الاشتراك
        #    - للجميع: صفحة انتهاء الاشتراك + تسجيل الخروج
        #    - للمدير فقط: صفحات التجديد/رفع الإيصال
        base_allowed = {
            reverse('reports:logout'),
            reverse('reports:subscription_expired'),
            # السماح بالتبديل حتى لا يعلق المستخدم على مدرسة منتهية
            reverse('reports:switch_school'),
        }

        # 4) جلب المدرسة النشطة (إن وُجدت) ثم عضوية المستخدم داخلها.
        #    هذا مهم لمنع ثغرة: مدير لديه أكثر من مدرسة، يجدد واحدة ثم يبدّل لأخرى منتهية.
        #    عدم وجود اشتراك يُعامل كمنتهي.
        from .models import SchoolMembership, School

        active_school = None
        try:
            sid = request.session.get("active_school_id")
            if sid:
                active_school = School.objects.filter(pk=sid, is_active=True).first()
        except Exception:
            active_school = None

        memberships_qs = (
            SchoolMembership.objects.filter(teacher=request.user, is_active=True)
            .select_related('school')
        )

        membership = None
        if active_school is not None:
            membership = memberships_qs.filter(school=active_school).first()
        if membership is None:
            membership = memberships_qs.first()

        # إن لم تكن لديه عضوية مدرسة، لا نطبق هذا المنع (نترك الصلاحيات الأخرى تتعامل)
        if membership is None:
            return self.get_response(request)

        # المدرسة التي سنفحص اشتراكها (المدرسة النشطة إن أمكن وإلا مدرسة العضوية الأولى)
        school = membership.school
        is_manager = membership.role_type == SchoolMembership.RoleType.MANAGER
        allowed_paths = set(base_allowed)
        if is_manager:
            allowed_paths |= {
                reverse('reports:my_subscription'),
                reverse('reports:payment_create'),
            }

        # السماح بهذه المسارات دائمًا لتجنب حلقات redirect
        if request.path in allowed_paths:
            return self.get_response(request)

        # 5) فحص انتهاء الاشتراك/غيابه
        subscription = None
        try:
            subscription = getattr(school, 'subscription', None)
        except Exception:
            subscription = None

        is_expired = True
        try:
            if subscription is not None:
                is_expired = bool(subscription.is_expired)
            else:
                # عدم وجود اشتراك يعني منتهي
                is_expired = True
        except Exception:
            is_expired = True

        if is_expired:
            # لو كان الطلب JSON/AJAX نرجع 403 بدل redirect
            try:
                accept = (request.headers.get("Accept") or "").lower()
                xrw = (request.headers.get("X-Requested-With") or "").lower()
                wants_json = "application/json" in accept or xrw == "xmlhttprequest"
            except Exception:
                wants_json = False
            if wants_json:
                return JsonResponse({"detail": "subscription_expired"}, status=403)
            return redirect('reports:subscription_expired')

        return self.get_response(request)


class ReportViewerAccessMiddleware:
    """يقيد حسابات (مشرف تقارير - عرض فقط) لمسارات القراءة فقط.

    الهدف: منع أي وصول لصفحات الإدارة/المعلمين/الطلبات/الإشعارات...
    والسماح فقط بعرض تقارير المدرسة (وقابلية الطباعة).

    ملاحظة:
    - لا نمنح is_staff ولا نخلط مع حسابات الأدمن.
    - هذا المنع دفاعي (Defense-in-depth) حتى لو نُسي تقييد view.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _wants_json(self, request) -> bool:
        try:
            accept = (request.headers.get("Accept") or "").lower()
            xrw = (request.headers.get("X-Requested-With") or "").lower()
            return ("application/json" in accept) or (xrw == "xmlhttprequest")
        except Exception:
            return False

    def _is_report_viewer(self, request, active_school_id: int | None) -> bool:
        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return False
        if getattr(user, "is_superuser", False) or getattr(user, "is_staff", False):
            return False
        try:
            from .models import SchoolMembership

            qs = SchoolMembership.objects.filter(
                teacher=user,
                role_type=SchoolMembership.RoleType.REPORT_VIEWER,
                is_active=True,
            )
            if active_school_id:
                qs = qs.filter(school_id=active_school_id)
            return qs.exists()
        except Exception:
            return False

    def __call__(self, request):
        # السماح بالملفات الثابتة والوسائط
        if request.path.startswith('/static/') or request.path.startswith('/media/'):
            return self.get_response(request)

        user = getattr(request, "user", None)
        if not getattr(user, "is_authenticated", False):
            return self.get_response(request)

        # نحدد المدرسة النشطة (إن وجدت)
        active_school_id = None
        try:
            active_school_id = request.session.get("active_school_id")
        except Exception:
            active_school_id = None

        # إن لم يكن مشرف تقارير، لا نتدخل
        if not self._is_report_viewer(request, active_school_id):
            return self.get_response(request)

        # منع أي عمليات كتابة تمامًا
        if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            if self._wants_json(request):
                return JsonResponse({"detail": "read_only_account"}, status=403)
            messages.error(request, "هذا الحساب للعرض فقط ولا يملك صلاحية تنفيذ عمليات.")
            return redirect("reports:school_reports_readonly")

        # مسارات مسموحة (قراءة فقط)
        allowed_paths = set()
        try:
            allowed_paths |= {
                reverse("reports:school_reports_readonly"),
                # ملف الإنجاز (قراءة فقط لمشرف التقارير)
                reverse("reports:achievement_school_files"),
                reverse("reports:achievement_school_teachers"),
                # نسمح بها لتسهيل redirect من views (لكن POST سيُمنع أعلاه)
                reverse("reports:achievement_my_files"),
                reverse("reports:logout"),
                reverse("reports:unread_notifications_count"),
                reverse("reports:subscription_expired"),
            }
        except Exception:
            pass

        path = request.path
        if path in allowed_paths:
            return self.get_response(request)

        # السماح فقط بطباعة التقرير ضمن مسار reports/*/print/
        if path.startswith("/reports/") and path.endswith("/print/"):
            return self.get_response(request)

        # السماح بعرض ملف الإنجاز + الطباعة/PDF (قراءة فقط)
        # /achievement/<pk>/
        # /achievement/<pk>/print/
        # /achievement/<pk>/pdf/
        if path.startswith("/achievement/"):
            tail = path[len("/achievement/"):]
            parts = [p for p in tail.split("/") if p]
            # أمثلة:
            #  - ['school']
            #  - ['school','teachers']
            #  - ['123']
            #  - ['123','print']
            #  - ['123','pdf']
            if parts and parts[0].isdigit():
                if len(parts) == 1:
                    return self.get_response(request)
                if len(parts) == 2 and parts[1] in {"print", "pdf"}:
                    return self.get_response(request)

        # أي شيء آخر: نعيد توجيه للصفحة المسموحة
        if self._wants_json(request):
            return JsonResponse({"detail": "forbidden"}, status=403)
        messages.info(request, "تم تقييد هذا الحساب للعرض على تقارير المدرسة فقط.")
        return redirect("reports:school_reports_readonly")


class ContentSecurityPolicyMiddleware:
    """Adds a Content Security Policy header in production.

    Notes:
    - This project uses inline <style>/<script> in templates, so we must allow
      'unsafe-inline' unless we migrate to nonces/hashes.
    - External fonts/icons are loaded via Google Fonts + cdnjs.
    - Cloudinary may serve media assets on res.cloudinary.com.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def _is_enabled(self) -> bool:
        try:
            if getattr(settings, "ENV", "development") == "production":
                return bool(getattr(settings, "CSP_ENABLED", True))
            return bool(getattr(settings, "CSP_ENABLED", False))
        except Exception:
            return False

    def _policy(self) -> str:
        # Kept for backwards-compat; prefer _policy_for_request
        return ""

    def _policy_for_request(self, request) -> str:
        # Allow override via env/settings for emergency tweaks.
        # If you provide a custom policy, you may include "{nonce}" placeholder.
        custom = (getattr(settings, "CONTENT_SECURITY_POLICY", "") or "").strip()
        if custom:
            try:
                return custom.format(nonce=getattr(request, "csp_nonce", ""))
            except Exception:
                return custom

        nonce = getattr(request, "csp_nonce", "")

        # Default policy: safe baseline with current template constraints.
        # NOTE: style-src keeps 'unsafe-inline' because templates use inline style="...".
        base = [
            "default-src 'self'",
            "base-uri 'self'",
            "form-action 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            f"script-src 'self' 'nonce-{nonce}'",
            f"script-src-elem 'self' 'nonce-{nonce}'",
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com https://cdnjs.cloudflare.com",
            "font-src 'self' data: https://fonts.gstatic.com https://cdnjs.cloudflare.com",
            "img-src 'self' data: blob: https: https://res.cloudinary.com",
            "connect-src 'self'",
            "upgrade-insecure-requests",
        ]
        return "; ".join(base)

    def __call__(self, request):
        # Generate per-request nonce early (so templates can use it)
        try:
            request.csp_nonce = secrets.token_urlsafe(16)
        except Exception:
            request.csp_nonce = ""

        response = self.get_response(request)

        if not self._is_enabled():
            return response

        # Avoid spending time on static/media responses
        try:
            if request.path.startswith("/static/") or request.path.startswith("/media/"):
                return response
        except Exception:
            pass

        # Do not enforce strict CSP on Django admin (it uses inline scripts without our nonce)
        try:
            if request.path.startswith("/admin/"):
                return response
        except Exception:
            pass

        header_name = "Content-Security-Policy"
        try:
            if bool(getattr(settings, "CSP_REPORT_ONLY", False)):
                header_name = "Content-Security-Policy-Report-Only"
        except Exception:
            pass

        # Don't override if already set by upstream/proxy
        if header_name not in response:
            response[header_name] = self._policy_for_request(request)

        return response
