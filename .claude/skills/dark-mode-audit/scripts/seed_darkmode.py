# -*- coding: utf-8 -*-
"""Seed a throwaway DB with enough data for the sweep to render real pages.

Point DB_NAME at a scratch file first so db.sqlite3 is never touched:

    export DB_NAME="$PWD/tmp/darkaudit.sqlite3"
    python manage.py migrate --noinput
    python .claude/skills/dark-mode-audit/scripts/seed_darkmode.py

The user is made staff+superuser so platform and maintenance screens render
instead of 403-ing — those carry some of the worst dark-mode breakage.
"""
import os
import sys

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')

sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
import django  # noqa: E402
django.setup()

from reports.models import (  # noqa: E402
    School, SchoolMembership, SchoolSubscription, SubscriptionPlan, Teacher, Ticket,
)

PHONE = os.environ.get('AUDIT_PHONE', '500000999')
PASSWORD = os.environ.get('AUDIT_PASSWORD', 'darkmode-check-1')

if 'darkaudit' not in (os.environ.get('DB_NAME') or ''):
    print('WARNING: DB_NAME does not look like a scratch database.')
    print('         Set DB_NAME before running so db.sqlite3 stays untouched.')

school, _ = School.objects.get_or_create(name='مدرسة الفحص', code='dark-check')
plan, _ = SubscriptionPlan.objects.get_or_create(
    name='خطة الفحص',
    defaults={'price': 0, 'days_duration': 365, 'max_teachers': 0})
SchoolSubscription.objects.get_or_create(school=school, defaults={'plan': plan})

user = Teacher.objects.filter(phone=PHONE).first()
if user is None:
    user = Teacher.objects.create_user(phone=PHONE, name='مدير الفحص', password=PASSWORD)
user.set_password(PASSWORD)
user.is_staff = True
user.is_superuser = True
user.save()

SchoolMembership.objects.get_or_create(
    school=school, teacher=user,
    defaults={'role_type': SchoolMembership.RoleType.MANAGER})

if not Ticket.objects.filter(school=school).exists():
    for title, status in [('طلب صيانة قاعة المصادر', Ticket.Status.OPEN),
                          ('طلب تجهيز معمل الحاسب', Ticket.Status.DONE)]:
        Ticket.objects.create(
            creator=user, assignee=user, school=school, is_platform=False,
            title=title, body='نصّ تجريبي لفحص الوضع الداكن.\nسطر ثانٍ.',
            status=status)

print('phone   ', PHONE)
print('password', PASSWORD)
print('school  ', school.id)
print('OK')
