from django.test import TestCase

from reports.models import (
    School,
    SchoolMembership,
    SchoolSubscription,
    SubscriptionPlan,
    Teacher,
)
from reports.permissions import effective_user_role_label, _school_role_labels
from reports.forms import _school_job_title_choices


class GenderedRoleLabelTests(TestCase):
    def setUp(self):
        self.boys = School.objects.create(
            name="مدرسة البنين", code="boys-school", gender=School.Gender.BOYS
        )
        self.girls = School.objects.create(
            name="مدرسة البنات", code="girls-school", gender=School.Gender.GIRLS
        )
        plan = SubscriptionPlan.objects.create(
            name="Plan", price=0, days_duration=30, max_teachers=0
        )
        SchoolSubscription.objects.create(school=self.boys, plan=plan)
        SchoolSubscription.objects.create(school=self.girls, plan=plan)

    def _make_member(self, school, role_type, job_title=None):
        t = Teacher.objects.create_user(
            phone=f"5{school.id}{role_type}{job_title or ''}"[:15],
            name="عضو",
            password="pass",
        )
        kwargs = dict(school=school, teacher=t, role_type=role_type)
        if job_title is not None:
            kwargs["job_title"] = job_title
        SchoolMembership.objects.create(**kwargs)
        return t

    def test_role_labels_map_boys(self):
        labels = _school_role_labels(self.boys)
        self.assertEqual(labels["manager"], "مدير المدرسة")
        self.assertEqual(labels["teacher"], "المعلم")
        self.assertEqual(labels["admin_staff"], "موظف إداري")
        self.assertEqual(labels["lab_tech"], "محضر مختبر")

    def test_role_labels_map_girls(self):
        labels = _school_role_labels(self.girls)
        self.assertEqual(labels["manager"], "مديرة المدرسة")
        self.assertEqual(labels["teacher"], "المعلمة")
        self.assertEqual(labels["admin_staff"], "موظفة إدارية")
        self.assertEqual(labels["lab_tech"], "محضرة مختبر")

    def test_effective_label_manager(self):
        boss_b = self._make_member(self.boys, SchoolMembership.RoleType.MANAGER)
        boss_g = self._make_member(self.girls, SchoolMembership.RoleType.MANAGER)
        self.assertEqual(
            effective_user_role_label(boss_b, active_school=self.boys), "مدير المدرسة"
        )
        self.assertEqual(
            effective_user_role_label(boss_g, active_school=self.girls), "مديرة المدرسة"
        )

    def test_effective_label_job_titles(self):
        for jt, boys_label, girls_label in [
            (SchoolMembership.JobTitle.TEACHER, "المعلم", "المعلمة"),
            (SchoolMembership.JobTitle.ADMIN_STAFF, "موظف إداري", "موظفة إدارية"),
            (SchoolMembership.JobTitle.LAB_TECH, "محضر مختبر", "محضرة مختبر"),
        ]:
            tb = self._make_member(self.boys, SchoolMembership.RoleType.TEACHER, jt)
            tg = self._make_member(self.girls, SchoolMembership.RoleType.TEACHER, jt)
            self.assertEqual(
                effective_user_role_label(tb, active_school=self.boys), boys_label
            )
            self.assertEqual(
                effective_user_role_label(tg, active_school=self.girls), girls_label
            )

    def test_job_title_choice_labels(self):
        boys_choices = dict(_school_job_title_choices(self.boys))
        girls_choices = dict(_school_job_title_choices(self.girls))
        self.assertEqual(boys_choices[SchoolMembership.JobTitle.TEACHER], "معلم")
        self.assertEqual(girls_choices[SchoolMembership.JobTitle.TEACHER], "معلمة")
        self.assertEqual(
            boys_choices[SchoolMembership.JobTitle.ADMIN_STAFF], "موظف إداري"
        )
        self.assertEqual(
            girls_choices[SchoolMembership.JobTitle.ADMIN_STAFF], "موظفة إدارية"
        )
        self.assertEqual(boys_choices[SchoolMembership.JobTitle.LAB_TECH], "محضر مختبر")
        self.assertEqual(
            girls_choices[SchoolMembership.JobTitle.LAB_TECH], "محضرة مختبر"
        )
