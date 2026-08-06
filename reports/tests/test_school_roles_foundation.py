# -*- coding: utf-8 -*-
"""أساس الأدوار المدرسية: الوكيل والموظف الإداري.

هذه المرحلة **توسعة لا تغيير**. الخاصية الحرجة فيها ليست ما تضيفه بل ما تُبقيه
على حاله: مدرسة قائمة لم يُسند فيها أي دور جديد يجب أن تتصرف بعدها كما كانت
حرفياً. ولذلك يبدأ الملف باختبارات «لم يتغير شيء» قبل اختبارات ما أُضيف.

والدرس المحفور في docs/REMOVE_SUPERVISOR_ROLES.md محروس هنا صراحةً: الدور
عضويةٌ في مدرسة بعينها لا عَلَمٌ على الحساب — والاختبار الذي يثبّت ذلك هو
``test_a_deputy_in_one_school_is_not_a_deputy_in_another``.
"""
from __future__ import annotations

from django.core.exceptions import ValidationError
from django.test import TestCase, override_settings
from django.urls import reverse

from reports.models import (
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.permissions import (
    effective_user_role_label,
    is_admin_staff,
    is_school_deputy,
    is_school_manager,
    is_school_staff,
    role_required,
    school_roles_for,
)


def _user(name: str, phone: str) -> Teacher:
    return Teacher.objects.create_user(phone=phone, name=name, password="Passw0rd!123")


def _school(name: str, code: str, *, seats: int = 0, gender: str = "boys") -> School:
    plan = SubscriptionPlan.objects.create(
        name=f"باقة {code}", price=0, days_duration=365, max_teachers=seats
    )
    school = School.objects.create(name=name, code=code, gender=gender)
    SchoolSubscription.objects.create(school=school, plan=plan)
    return school


class RoleModelShapeTests(TestCase):
    """شكل النموذج بعد التوسعة."""

    def test_staff_roles_covers_every_non_manager_role(self):
        """أي دور جديد يُضاف ولا يدخل STAFF_ROLES يختفي بصمت من كشوف المدرسة."""
        all_roles = set(SchoolMembership.RoleType.values)
        expected_staff = all_roles - {SchoolMembership.RoleType.MANAGER}

        self.assertEqual(set(SchoolMembership.STAFF_ROLES), expected_staff)

    def test_manager_never_consumes_a_seat(self):
        self.assertNotIn(SchoolMembership.RoleType.MANAGER, SchoolMembership.SEAT_CONSUMING_ROLES)

    def test_every_staff_role_consumes_a_seat(self):
        """القرار التجاري المُعلن: كل منسوب يستهلك مقعداً، والمدير وحده مستثنى."""
        self.assertEqual(
            set(SchoolMembership.SEAT_CONSUMING_ROLES),
            set(SchoolMembership.STAFF_ROLES),
        )


class SeatCountingTests(TestCase):
    """المقاعد تُعدّ بالمنسوبين لا بالعضويات."""

    def setUp(self):
        self.school = _school("مدرسة المقاعد", "seats", seats=3)

    def _add(self, user, role):
        return SchoolMembership.objects.create(school=self.school, teacher=user, role_type=role)

    def test_a_plain_teacher_occupies_one_seat(self):
        self._add(_user("معلم", "0500001001"), SchoolMembership.RoleType.TEACHER)
        self.assertEqual(SchoolMembership.seats_used(self.school), 1)

    def test_the_manager_is_outside_the_count(self):
        self._add(_user("المدير", "0500001002"), SchoolMembership.RoleType.MANAGER)
        self.assertEqual(SchoolMembership.seats_used(self.school), 0)

    def test_one_person_holding_two_roles_still_occupies_one_seat(self):
        """وكيل له نصاب تدريسي شخص واحد — وعدّ الصفوف كان يحتسبه اثنين."""
        person = _user("وكيل ومعلم", "0500001003")
        self._add(person, SchoolMembership.RoleType.DEPUTY)
        self._add(person, SchoolMembership.RoleType.TEACHER)

        self.assertEqual(SchoolMembership.seats_used(self.school), 1)

    def test_a_second_role_is_allowed_even_when_the_plan_is_full(self):
        """المقاعد ممتلئة، لكن الدور الثاني لمنسوب قائم لا يطلب مقعداً جديداً."""
        people = [_user(f"منسوب {i}", f"050000200{i}") for i in range(3)]
        for person in people:
            self._add(person, SchoolMembership.RoleType.TEACHER)
        self.assertEqual(SchoolMembership.seats_used(self.school), 3)

        # ترقية أحدهم إلى وكيل مع بقاء نصابه — يجب أن تمر.
        self._add(people[0], SchoolMembership.RoleType.DEPUTY)
        self.assertEqual(SchoolMembership.seats_used(self.school), 3)

    def test_a_brand_new_person_is_refused_when_the_plan_is_full(self):
        for i in range(3):
            self._add(_user(f"منسوب {i}", f"050000300{i}"), SchoolMembership.RoleType.TEACHER)

        with self.assertRaises(ValidationError):
            self._add(_user("زائد", "0500003099"), SchoolMembership.RoleType.TEACHER)

    def test_a_new_deputy_also_consumes_a_seat(self):
        """قرار التسعير المعتمد: الوكيل ليس مجاناً."""
        for i in range(3):
            self._add(_user(f"منسوب {i}", f"050000400{i}"), SchoolMembership.RoleType.TEACHER)

        with self.assertRaises(ValidationError):
            self._add(_user("وكيل جديد", "0500004099"), SchoolMembership.RoleType.DEPUTY)

    def test_bulk_seat_counts_match_the_single_school_count(self):
        """نسخة الجملة الواحدة يجب ألا تفترق عن نسخة المدرسة الواحدة."""
        other = _school("مدرسة أخرى", "other-seats", seats=0)
        person = _user("مزدوج", "0500005001")
        self._add(person, SchoolMembership.RoleType.DEPUTY)
        self._add(person, SchoolMembership.RoleType.TEACHER)
        SchoolMembership.objects.create(
            school=other,
            teacher=_user("منسوب آخر", "0500005002"),
            role_type=SchoolMembership.RoleType.TEACHER,
        )

        bulk = SchoolMembership.seats_used_by_school([self.school.pk, other.pk])

        self.assertEqual(bulk[self.school.pk], SchoolMembership.seats_used(self.school))
        self.assertEqual(bulk[other.pk], SchoolMembership.seats_used(other))


class RoleScopingTests(TestCase):
    """الدور عضوية في مدرسة — لا عَلَم على الحساب."""

    def setUp(self):
        self.here = _school("مدرسة الوكيل", "deputy-here")
        self.there = _school("مدرسة أخرى", "deputy-there")
        self.person = _user("الوكيل", "0500006001")
        SchoolMembership.objects.create(
            school=self.here,
            teacher=self.person,
            role_type=SchoolMembership.RoleType.DEPUTY,
        )

    def test_a_deputy_is_recognised_in_their_own_school(self):
        self.assertTrue(is_school_deputy(self.person, self.here))

    def test_a_deputy_in_one_school_is_not_a_deputy_in_another(self):
        """الخطأ الذي كلّف المشروع 180 موضعاً: الدور بلا نطاق يتسرب."""
        self.assertFalse(is_school_deputy(self.person, self.there))

    def test_a_role_question_without_a_school_answers_no(self):
        """بلا مدرسة لا معنى للسؤال — والإجابة بنعم تُعيد اختراع العَلَم العام."""
        self.assertFalse(is_school_deputy(self.person))

    def test_a_deputy_is_not_a_manager(self):
        """الوكيل ليس مديراً مصغَّراً؛ الاعتماد النهائي يبقى للمدير."""
        self.assertFalse(is_school_manager(self.person, self.here))

    def test_a_deputy_counts_as_school_staff(self):
        self.assertTrue(is_school_staff(self.person, self.here))

    def test_dual_roles_are_both_reported(self):
        SchoolMembership.objects.create(
            school=self.here,
            teacher=self.person,
            role_type=SchoolMembership.RoleType.TEACHER,
        )
        self.assertEqual(
            school_roles_for(self.person, self.here),
            {SchoolMembership.RoleType.DEPUTY, SchoolMembership.RoleType.TEACHER},
        )

    def test_admin_staff_is_detected(self):
        clerk = _user("موظف", "0500006002")
        SchoolMembership.objects.create(
            school=self.here,
            teacher=clerk,
            role_type=SchoolMembership.RoleType.ADMIN_STAFF,
        )
        self.assertTrue(is_admin_staff(clerk, self.here))
        self.assertFalse(is_school_deputy(clerk, self.here))


class MembershipPrefetchTests(TestCase):
    """التهيئة المسبقة للعضويات تُغطي كل ما تسأله دالة التسمية.

    هذا النوع من الأعطال لا يُصدر خطأً ولا نتيجة خاطئة — يعود فقط استعلامٌ لكل
    صف في الكشف. فلا يكشفه إلا اختبار يعدّ الاستعلامات.
    """

    def setUp(self):
        self.school = _school("مدرسة الكشف", "prefetch")
        self.people = []
        roles = [
            SchoolMembership.RoleType.MANAGER,
            SchoolMembership.RoleType.DEPUTY,
            SchoolMembership.RoleType.ADMIN_STAFF,
            SchoolMembership.RoleType.TEACHER,
            SchoolMembership.RoleType.TEACHER,
        ]
        for index, role in enumerate(roles):
            person = _user(f"منسوب {index}", f"05000090{index:02d}")
            SchoolMembership.objects.create(
                school=self.school, teacher=person, role_type=role
            )
            self.people.append(person)

    def test_labelling_a_whole_roster_costs_a_single_query(self):
        from reports.permissions import prefetch_memberships_for_school

        # الاستعلام الوحيد المسموح هو استعلام التهيئة نفسه.
        with self.assertNumQueries(1):
            prefetch_memberships_for_school(self.people, self.school)
            for person in self.people:
                effective_user_role_label(person, self.school)

    def test_prefetched_labels_match_the_uncached_ones(self):
        """السرعة لا تُشترى بنتيجة مختلفة."""
        from reports.permissions import prefetch_memberships_for_school

        expected = [
            effective_user_role_label(Teacher.objects.get(pk=person.pk), self.school)
            for person in self.people
        ]

        fresh = list(Teacher.objects.filter(pk__in=[p.pk for p in self.people]).order_by("pk"))
        prefetch_memberships_for_school(fresh, self.school)
        actual = [effective_user_role_label(person, self.school) for person in fresh]

        self.assertEqual(actual, expected)


class RoleLabelTests(TestCase):
    """التسمية المعروضة تتبع الدور، وتتبع نوع المدرسة."""

    def test_deputy_label_in_a_boys_school(self):
        school = _school("ثانوية البنين", "boys-lbl", gender="boys")
        person = _user("سعد", "0500007001")
        SchoolMembership.objects.create(
            school=school, teacher=person, role_type=SchoolMembership.RoleType.DEPUTY
        )
        self.assertEqual(effective_user_role_label(person, school), "وكيل المدرسة")

    def test_deputy_label_in_a_girls_school(self):
        school = _school("ثانوية البنات", "girls-lbl", gender="girls")
        person = _user("سعاد", "0500007002")
        SchoolMembership.objects.create(
            school=school, teacher=person, role_type=SchoolMembership.RoleType.DEPUTY
        )
        self.assertEqual(effective_user_role_label(person, school), "وكيلة المدرسة")

    def test_deputy_label_wins_over_a_concurrent_teaching_role(self):
        """وكيل له نصاب تدريسي يظل وكيلاً في الترويسة، لا معلّماً."""
        school = _school("مدرسة مزدوجة", "dual-lbl")
        person = _user("فهد", "0500007003")
        SchoolMembership.objects.create(
            school=school, teacher=person, role_type=SchoolMembership.RoleType.DEPUTY
        )
        SchoolMembership.objects.create(
            school=school, teacher=person, role_type=SchoolMembership.RoleType.TEACHER
        )
        self.assertEqual(effective_user_role_label(person, school), "وكيل المدرسة")

    def test_admin_staff_role_label(self):
        school = _school("مدرسة الموظفين", "clerk-lbl")
        person = _user("ناصر", "0500007004")
        SchoolMembership.objects.create(
            school=school, teacher=person, role_type=SchoolMembership.RoleType.ADMIN_STAFF
        )
        self.assertEqual(effective_user_role_label(person, school), "موظف إداري")

    def test_an_untouched_teacher_keeps_the_exact_label_it_had(self):
        """اختبار «لم يتغير شيء»: مدرسة لم تُسند فيها الأدوار الجديدة."""
        school = _school("مدرسة تقليدية", "legacy-lbl")
        person = _user("معلم", "0500007005")
        SchoolMembership.objects.create(
            school=school, teacher=person, role_type=SchoolMembership.RoleType.TEACHER
        )
        self.assertEqual(effective_user_role_label(person, school), "المعلم")

    def test_the_legacy_admin_staff_job_title_still_labels_correctly(self):
        """المسمّى الوظيفي القديم لم يُهجَّر بعد، فيجب أن يظل يعمل كما كان."""
        school = _school("مدرسة قديمة", "legacy-job")
        person = _user("موظف قديم", "0500007006")
        SchoolMembership.objects.create(
            school=school,
            teacher=person,
            role_type=SchoolMembership.RoleType.TEACHER,
            job_title=SchoolMembership.JobTitle.ADMIN_STAFF,
        )
        self.assertEqual(effective_user_role_label(person, school), "موظف إداري")


class RoleRequiredTests(TestCase):
    """الحارس: يعرف ما يُسمّى له، ويرفض ما لا يعرفه صراحةً."""

    def test_an_unknown_role_name_fails_loudly_at_import_time(self):
        """النسخة السابقة كانت تتجاهل الاسم المجهول وتمنع الجميع بلا سبب ظاهر."""
        with self.assertRaises(ValueError):
            role_required({"wizard"})

    def test_every_supported_role_is_accepted(self):
        for role in ("manager", "deputy", "admin_staff", "staff"):
            self.assertTrue(callable(role_required({role})))


@override_settings(ALLOWED_HOSTS=["testserver"])
class ManagerAccessIsUnchangedTests(TestCase):
    """أهم اختبار في الملف: صلاحية المدير لم تتأثر بإعادة كتابة الحارس."""

    def setUp(self):
        self.school = _school("مدرسة الحارس", "guard")
        self.manager = _user("المدير", "0500008001")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=self.manager,
            role_type=SchoolMembership.RoleType.MANAGER,
        )
        self.teacher = _user("معلم", "0500008002")
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

    def test_the_manager_still_reaches_a_manager_only_page(self):
        self._enter(self.manager)
        response = self.client.get(reverse("reports:manage_teachers"))
        self.assertEqual(response.status_code, 200)

    def test_a_teacher_is_still_refused(self):
        self._enter(self.teacher)
        response = self.client.get(reverse("reports:manage_teachers"))
        self.assertEqual(response.status_code, 302)

    def test_a_deputy_gets_no_manager_powers_merely_by_holding_the_role(self):
        """حمل الدور لا يمنح شيئاً بذاته — النطاق والتفويض يأتيان لاحقاً."""
        deputy = _user("الوكيل", "0500008003")
        SchoolMembership.objects.create(
            school=self.school,
            teacher=deputy,
            role_type=SchoolMembership.RoleType.DEPUTY,
        )
        self._enter(deputy)

        response = self.client.get(reverse("reports:manage_teachers"))
        self.assertEqual(response.status_code, 302)
