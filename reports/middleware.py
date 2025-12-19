from django.conf import settings
from django.contrib.auth import logout
from django.http import JsonResponse
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.contrib import messages


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
            subscription = school.subscription
        except Exception:
            subscription = None

        is_expired = True
        try:
            if subscription is not None:
                is_expired = bool(subscription.is_expired)
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
