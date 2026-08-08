# -*- coding: utf-8 -*-
"""اتّساق عضوية المنسوب بعد دخول الأدوار الخمسة.

الأدوار دخلت من شاشة الأدوار، وبقيت الشاشات الأقدم تقرأ الدنيا بعينٍ واحدة:
``role_type=TEACHER``. فنشأ عن ذلك أربعة انفصالات تُثبّتها الاختبارات هنا بعد
إصلاحها — كلٌّ منها يفشل بصمت لا بخطأ، وهذا سبب كتابتها:

- **المسمّى يقرّر الدور من كل باب.** محضّر مختبر يخرج من «إضافة مستخدم»
  ``TEACHER`` ومن شاشة الأدوار ``ADMIN_STAFF``: اسمٌ واحد وصلاحيتان.
- **المنسوب لا المعلّم.** كشف المنسوبين يقرأ ``STAFF_ROLES``، وزرّا التعديل
  والحذف كانا يقرآن ``TEACHER`` — فيظهر الصفّ بزرّين يردّانه.
- **الحذف يزيل كل الأدوار.** إزالة صفّ ``TEACHER`` وحده تترك وكيلاً بوكالته
  والرسالة تقول إنه أُزيل.
- **المقعد للشخص لا للصفّ.** عدّ صفوف ``TEACHER`` يُبقي الوكيل والموظف خارج
  حدّ الباقة، ويجعل رقم هذه الشاشة مخالفاً لرقم لوحة الاستهلاك.
"""
from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from reports import capabilities as caps
from reports.models import (
    School,
    SchoolMembership,
    SchoolSubscription,
    StaffScope,
    SubscriptionPlan,
    Teacher,
)


def _school(name: str, code: str, *, seats: int = 20) -> School:
    plan = SubscriptionPlan.objects.create(
        name=f"باقة {code}", price=0, days_duration=365, max_teachers=seats
    )
    school = School.objects.create(name=name, code=code)
    SchoolSubscription.objects.create(school=school, plan=plan)
    return school


class JobTitleDecidesRoleTests(TestCase):
    """قاعدة النموذج الواحدة — يسألها كل باب يُنشئ عضوية."""

    def test_the_lab_technician_is_admin_staff_by_authority(self):
        self.assertEqual(
            SchoolMembership.role_for_job_title(SchoolMembership.JobTitle.LAB_TECH),
            SchoolMembership.RoleType.ADMIN_STAFF,
        )

    def test_the_administrative_employee_is_admin_staff(self):
        self.assertEqual(
            SchoolMembership.role_for_job_title(SchoolMembership.JobTitle.ADMIN_STAFF),
            SchoolMembership.RoleType.ADMIN_STAFF,
        )

    def test_the_teacher_stays_a_teacher(self):
        self.assertEqual(
            SchoolMembership.role_for_job_title(SchoolMembership.JobTitle.TEACHER),
            SchoolMembership.RoleType.TEACHER,
        )

    def test_an_unknown_title_falls_back_to_the_narrowest_role(self):
        """الافتراض الأضيق صلاحيةً هو الآمن: مسمّى مجهول لا يمنح صلاحية موظف."""
        self.assertEqual(
            SchoolMembership.role_for_job_title("something_new"),
            SchoolMembership.RoleType.TEACHER,
        )

    def test_the_roles_screen_reads_the_same_rule(self):
        """جدول شاشة الأدوار مشتقٌّ من القاعدة لا مكتوبٌ بجوارها."""
        from reports.forms_staff_roles import ASSIGNMENTS

        role, job_title = ASSIGNMENTS[SchoolMembership.JobTitle.LAB_TECH]
        self.assertEqual(role, SchoolMembership.role_for_job_title(job_title))


class ManagingStaffWhoAreNotTeachersTests(TestCase):
    """المدير يدير منسوبيه جميعاً لا معلّميه وحدهم."""

    def setUp(self):
        self.school = _school("مدرسة المنسوبين", "staff-consistency")
        self.manager = Teacher.objects.create_user(
            phone="0500070001", name="المدير", password="Passw0rd!123", is_staff=True
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        # محضّر مختبر كما تُنشئه شاشة الأدوار: بلا عضوية تدريسية.
        self.lab_tech = Teacher.objects.create_user(
            phone="0500070002", name="المحضّر", password="Passw0rd!123"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.lab_tech,
            role_type=SchoolMembership.RoleType.ADMIN_STAFF,
            job_title=SchoolMembership.JobTitle.LAB_TECH,
        )
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def test_the_lab_technician_appears_in_the_staff_list(self):
        response = self.client.get(reverse("reports:manage_teachers"))
        self.assertContains(response, "المحضّر")

    def test_the_manager_can_open_the_lab_technicians_edit_screen(self):
        """كان الكشف يعرضه والزرّ يردّه بأنه «غير مرتبط بمدرستك»."""
        response = self.client.get(
            reverse("reports:edit_teacher", args=[self.lab_tech.pk])
        )
        self.assertEqual(response.status_code, 200)

    def test_removing_the_lab_technician_actually_removes_them(self):
        response = self.client.post(
            reverse("reports:delete_teacher", args=[self.lab_tech.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            SchoolMembership.objects.filter(
                school=self.school, teacher=self.lab_tech
            ).exists()
        )

    def test_removing_a_dual_role_member_leaves_no_role_behind(self):
        """الوكيل ذو النصاب التدريسي: حذف صفّ ``TEACHER`` وحده يُبقي وكالته."""
        deputy = Teacher.objects.create_user(
            phone="0500070003", name="الوكيل", password="Passw0rd!123"
        )
        for role in (
            SchoolMembership.RoleType.DEPUTY,
            SchoolMembership.RoleType.TEACHER,
        ):
            SchoolMembership.objects.create(
                school=self.school, teacher=deputy, role_type=role
            )

        self.client.post(reverse("reports:delete_teacher", args=[deputy.pk]))

        self.assertFalse(
            SchoolMembership.objects.filter(
                school=self.school, teacher=deputy
            ).exists()
        )

    def test_an_outsider_is_still_refused(self):
        outsider = Teacher.objects.create_user(
            phone="0500070004", name="غريب", password="Passw0rd!123"
        )

        response = self.client.post(
            reverse("reports:delete_teacher", args=[outsider.pk])
        )

        self.assertEqual(response.status_code, 302)
        self.assertTrue(Teacher.objects.filter(pk=outsider.pk).exists())


class SeatsCountPeopleNotTeacherRowsTests(TestCase):
    """حدّ الباقة يُقاس بالمقاعد، ومصدرها ``seats_used`` وحده."""

    def setUp(self):
        self.school = _school("مدرسة المقاعد", "seat-consistency", seats=2)
        self.manager = Teacher.objects.create_user(
            phone="0500080001", name="المدير", password="Passw0rd!123", is_staff=True
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.client.force_login(self.manager)
        session = self.client.session
        session["active_school_id"] = self.school.pk
        session.save()

    def _member(self, phone, name, role):
        member = Teacher.objects.create_user(
            phone=phone, name=name, password="Passw0rd!123"
        )
        SchoolMembership.objects.create(
            school=self.school, teacher=member, role_type=role
        )
        return member

    def test_deputies_and_admin_staff_fill_the_seats_too(self):
        """كان عدّ صفوف ``TEACHER`` يُبقيهما خارج الحساب فتُتجاوز الباقة."""
        self._member("0500080002", "الوكيل", SchoolMembership.RoleType.DEPUTY)
        self._member("0500080003", "الموظف", SchoolMembership.RoleType.ADMIN_STAFF)

        self.assertEqual(SchoolMembership.seats_used(self.school), 2)

        self.client.post(
            reverse("reports:add_teacher"),
            {
                "name": "الزائد",
                "phone": "0500080004",
                "national_id": "",
                "job_title": SchoolMembership.JobTitle.TEACHER,
                "is_active": "on",
            },
        )

        self.assertFalse(Teacher.objects.filter(phone="0500080004").exists())

    def test_a_dual_role_member_occupies_one_seat_not_two(self):
        member = self._member(
            "0500080005", "الوكيل المدرّس", SchoolMembership.RoleType.DEPUTY
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=member,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

        self.assertEqual(SchoolMembership.seats_used(self.school), 1)


class ReviewersReachTheApprovalInboxTests(TestCase):
    """من يملك صلاحية المراجعة يجد بابها في القائمة."""

    def setUp(self):
        self.school = _school("مدرسة المراجعة", "review-nav")
        self.manager = Teacher.objects.create_user(
            phone="0500090001", name="المدير", password="Passw0rd!123"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.deputy = Teacher.objects.create_user(
            phone="0500090002", name="الوكيل", password="Passw0rd!123"
        )
        self.deputy_membership = SchoolMembership.objects.create(
            school=self.school,
            teacher=self.deputy,
            role_type=SchoolMembership.RoleType.DEPUTY,
        )
        self.teacher = Teacher.objects.create_user(
            phone="0500090003", name="المعلّم", password="Passw0rd!123"
        )
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.teacher,
            role_type=SchoolMembership.RoleType.TEACHER,
        )

    def _home_as(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("reports:home"))

    def test_a_deputy_granted_review_finds_the_inbox_in_the_menu(self):
        StaffScope.objects.create(
            membership=self.deputy_membership, capabilities=[caps.REVIEW_REPORTS]
        )

        response = self._home_as(self.deputy)

        self.assertTrue(response.context["CAN_REVIEW_APPROVALS"])
        self.assertContains(response, reverse("reports:approval_inbox"))

    def test_a_deputy_without_the_capability_sees_no_link(self):
        response = self._home_as(self.deputy)

        self.assertFalse(response.context["CAN_REVIEW_APPROVALS"])
        self.assertNotContains(response, reverse("reports:approval_inbox"))

    def test_a_plain_teacher_sees_no_link(self):
        response = self._home_as(self.teacher)

        self.assertFalse(response.context["CAN_REVIEW_APPROVALS"])
        self.assertNotContains(response, reverse("reports:approval_inbox"))
