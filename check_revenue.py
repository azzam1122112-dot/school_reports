import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from reports.models import Payment
from django.db.models import Sum

# التحقق من الدفعات المعتمدة
approved_payments = Payment.objects.filter(status=Payment.Status.APPROVED)
print(f'عدد الدفعات المعتمدة: {approved_payments.count()}')
total = approved_payments.aggregate(total=Sum('amount'))['total']
print(f'إجمالي المبالغ: {total} ر.س')

print('\nتفاصيل الدفعات:')
for p in approved_payments[:10]:
    school_name = p.school.name if p.school else "غير محدد"
    print(f'  - {p.amount} ر.س (المدرسة: {school_name}, التاريخ: {p.created_at.strftime("%Y-%m-%d")})')

# التحقق من جميع الدفعات (بغض النظر عن الحالة)
all_payments = Payment.objects.all()
print(f'\n--- جميع الدفعات ---')
print(f'العدد الكلي: {all_payments.count()}')
all_total = all_payments.aggregate(total=Sum('amount'))['total']
print(f'الإجمالي الكلي: {all_total} ر.س')
