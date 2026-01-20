
import os
import django
from django.conf import settings
from django.db.models import Q, Count

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from reports.models import Ticket, School, Teacher, DepartmentMembership
from reports.views import _user_department_codes, _filter_by_school

# Mock request/user
user = Teacher.objects.first()
if not user:
    print("No user found.")
    exit()

active_school = School.objects.filter(is_active=True).first()

print(f"User: {user}, School: {active_school}")

user_codes = []
# Simulate _user_department_codes
if DepartmentMembership:
    codes = set()
    mem_qs = DepartmentMembership.objects.filter(teacher=user)
    if active_school is not None:
        mem_qs = mem_qs.filter(department__school=active_school)
    mem_codes = mem_qs.values_list("department__slug", flat=True)
    for c in mem_codes:
        if c:
            codes.add(c)
    user_codes = list(codes)

print(f"User codes: {user_codes}")

qs = Ticket.objects.select_related("creator", "assignee", "department").prefetch_related("recipients").filter(
    Q(assignee=user)
    | Q(recipients=user)
    | Q(assignee__isnull=True, department__slug__in=user_codes)
).distinct()

if active_school and hasattr(Ticket, 'school'):
    qs = qs.filter(school=active_school)

# Simulate _filter_by_school logic if it's external (I copied logic above assuming Ticket has school)
# Let's check _filter_by_school implementation later if needed.

# Simulate ordering
order = "-created_at"
qs = qs.order_by(order, "-id")

print("Executing main query...")
print(f"Count: {qs.count()}")

print("Executing stats query...")
try:
    # This is the line I suspect
    counts_qs = qs.values("status").annotate(c=Count("id")).values_list("status", "c")
    print(f"Stats query SQL: {counts_qs.query}")
    raw_counts = dict(counts_qs)
    print(f"Raw counts: {raw_counts}")
except Exception as e:
    print(f"Stats query failed: {e}")
    import traceback
    traceback.print_exc()

print("Done.")
