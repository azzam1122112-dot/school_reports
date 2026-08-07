# -*- coding: utf-8 -*-
"""شريط تنقّل المدير: مجموعات قليلة بدل اثني عشر تبويباً مسطّحاً.

الشريط القديم كان يعرض كل شاشة تبويباً قائماً بذاته، فامتلأ حتى ركب على اسم
المدرسة، واختفى كاملاً دون 1600px فلم يبق للمدير في حاسوبه المحمول إلا زر
القائمة الجانبية. وهذه الاختبارات تحرس ثلاثة أشياء: أن الشريط بقي قصيراً، وأن
شيئاً من روابطه لم يسقط في أثناء التجميع، وأن غير المدير لم يمسّه التغيير.
"""
from __future__ import annotations

import re

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from reports.models import School, SchoolMembership, SchoolSubscription, SubscriptionPlan

User = get_user_model()


def _school(name: str, code: str) -> School:
    plan = SubscriptionPlan.objects.create(
        name=f"باقة {code}", price=0, days_duration=365, max_teachers=0
    )
    school = School.objects.create(name=name, code=code)
    SchoolSubscription.objects.create(school=school, plan=plan)
    return school


def _user(name: str, phone: str) -> User:
    return User.objects.create_user(phone=phone, name=name, password="strong-pass-123")


class ManagerHeaderNavigationTests(TestCase):
    def setUp(self):
        self.school = _school("ثانوية الشريط", "header-school")
        self.manager = _user("مدير الشريط", "0500014001")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.teacher = _user("معلم الشريط", "0500014002")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

    def _enter(self, user):
        self.client.force_login(user)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def _page(self, user, url_name: str) -> str:
        self._enter(user)
        return self.client.get(reverse(url_name)).content.decode()

    def _header(self, user, url_name: str = "reports:admin_dashboard") -> str:
        """الشريط وحده — لا بقية الصفحة، فلا يخلط الاختبار روابط المحتوى بروابطه."""
        nav = re.search(r'<nav class="hdr-nav".*?</nav>', self._page(user, url_name), re.S)
        self.assertIsNotNone(nav, "شريط التنقّل غير موجود في الصفحة")
        return nav.group(0)

    @staticmethod
    def _shell_tag(page: str) -> str:
        """وسم ``<header>`` نفسه — لا نصّ الأنماط الذي يذكر الصنف أيضاً."""
        match = re.search(r"<header[^>]*>", page)
        return match.group(0) if match else ""

    def test_the_manager_bar_stays_short(self):
        """الازدحام هو العلّة: ستة مداخل عليا لا أكثر."""
        header = self._header(self.manager)
        direct_tabs = len(re.findall(r'<a class="tab ', header))
        groups = header.count("data-nav-group")
        self.assertLessEqual(direct_tabs + groups, 6, "شريط المدير عاد إلى الازدحام")

    def test_no_manager_destination_was_lost_in_the_grouping(self):
        """التجميع إخفاءٌ للضجيج لا للوجهات."""
        header = self._header(self.manager)
        for name in (
            "reports:manage_teachers",
            "reports:departments_list",
            "reports:staff_roles",
            "reports:assignment_board",
            "reports:meeting_list",
            "reports:plan_list",
            "reports:initiative_list",
            "reports:document_archive",
            "reports:achievement_school_files",
            "reports:approval_inbox",
            "reports:notifications_sent",
            "reports:circular_draft_list",
            "reports:manager_school_tickets",
        ):
            with self.subTest(destination=name):
                self.assertIn(f'href="{reverse(name)}"', header)

    def test_approval_stays_a_single_click(self):
        """عمل المدير اليومي لا يُدفن تحت نقرة ثانية."""
        header = self._header(self.manager)
        approval = reverse("reports:approval_inbox")
        self.assertRegex(header, rf'class="tab [^"]*"\s+href="{re.escape(approval)}"')

    def test_every_group_is_operable_by_keyboard_and_screen_reader(self):
        header = self._header(self.manager)
        self.assertEqual(header.count("data-nav-group"), header.count('aria-haspopup="true"'))
        self.assertEqual(header.count("data-nav-group"), header.count('aria-expanded="false"'))
        self.assertEqual(header.count('class="nav-pop"'), header.count('role="menu"'))

    def test_a_teacher_keeps_the_flat_bar(self):
        """التجميع علاجٌ لازدحام المدير، والمعلّم شريطه قصير أصلاً."""
        header = self._header(self.teacher, "reports:home")
        self.assertNotIn("data-nav-group", header)

    def test_the_grouped_shell_is_marked_only_for_the_manager(self):
        """الوسم هو ما يؤخّر انهيار الشريط إلى 1280px، فلا يُمنح لشريط طويل."""
        manager_shell = self._shell_tag(self._page(self.manager, "reports:admin_dashboard"))
        self.assertIn("hdr-grouped", manager_shell)

        teacher_shell = self._shell_tag(self._page(self.teacher, "reports:home"))
        self.assertNotIn("hdr-grouped", teacher_shell)
