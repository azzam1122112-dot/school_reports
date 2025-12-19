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
        # 1. تجاوز الفحص للمستخدمين غير المسجلين أو المدراء النظام (Superusers)
        if not request.user.is_authenticated or request.user.is_superuser:
            return self.get_response(request)

        # 2. تحديد المسارات المسموح بها دائماً (Logout, Expired Page, Payments, Support)
        allowed_paths = [
            reverse('reports:logout'),
            reverse('reports:subscription_expired'),
            # سنضيف مسارات الدفع والدعم لاحقاً هنا إذا لزم الأمر
            # لكن مبدئياً سنسمح للمدير فقط بالوصول لها
        ]
        
        # السماح بمسارات الدفع والدعم الفني لمدير المدرسة فقط
        # سنفترض أن أسماء الـ URL ستكون كالتالي (سننشئها لاحقاً)
        try:
            allowed_paths.append(reverse('reports:my_subscription'))
            allowed_paths.append(reverse('reports:payment_create'))
            allowed_paths.append(reverse('reports:my_support_tickets')) # الدعم الفني
            allowed_paths.append(reverse('reports:support_ticket_create'))
        except:
            pass # قد لا تكون الروابط موجودة بعد

        if request.path in allowed_paths:
            return self.get_response(request)
            
        # السماح بالملفات الثابتة والوسائط
        if request.path.startswith('/static/') or request.path.startswith('/media/'):
            return self.get_response(request)

        # 3. جلب اشتراك المدرسة
        # نفترض أن المستخدم مرتبط بمدرسة واحدة نشطة عبر SchoolMembership
        # أو نستخدم أول مدرسة نشطة يجدها
        
        # استيراد النماذج هنا لتجنب Circular Import
        from .models import SchoolMembership

        membership = SchoolMembership.objects.filter(
            teacher=request.user, 
            is_active=True
        ).select_related('school__subscription').first()

        if membership and hasattr(membership.school, 'subscription'):
            subscription = membership.school.subscription
            
            # 4. فحص الانتهاء
            if subscription.is_expired:
                # إذا كان المستخدم مدير مدرسة، نسمح له بالوصول لصفحات التجديد والدعم
                is_manager = (membership.role_type == SchoolMembership.RoleType.MANAGER)
                
                if is_manager:
                    # إذا كان المسار الحالي هو أحد مسارات الإدارة المسموحة، دعه يمر
                    # (تمت إضافتها في allowed_paths أعلاه، لكن يمكننا التدقيق أكثر هنا)
                    pass 
                else:
                    # المعلمون: حجب كامل وتوجيه لصفحة الانتهاء
                    return redirect('reports:subscription_expired')

                # للمدير أيضاً، إذا حاول دخول صفحات أخرى (مثل التقارير)، نوجهه لصفحة الانتهاء
                # إلا إذا كان في المسارات المسموحة
                if request.path not in allowed_paths:
                     return redirect('reports:subscription_expired')

        return self.get_response(request)
