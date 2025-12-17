from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.contrib import messages

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
